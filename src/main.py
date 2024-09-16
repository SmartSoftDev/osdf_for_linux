#!/usr/bin/env python3
"""
Tool to quickly convert from JSON to YAML and vice-versa. 
"""

from collections import OrderedDict
import os
import json
import argparse
import logging
import subprocess
from datetime import datetime, timezone


class UnsupportedConfigFileExtension(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class App:
    def __init__(self) -> None:
        self.args = None
        self.l = None
        self.out_dir = None
        self.packages = None
        self.packages_info = None
        self.data_format_version = "v1.0.0"
        self.discover_issues = {"warnings": [], "errors": []}

    def args_parse(self):
        parser = argparse.ArgumentParser(description="convert to json")
        parser.add_argument(
            "-v",
            "--verbose",
            action="count",
            default=0,
            help="Enable debugging (count up to 3 levels)",
        )
        subparsers = parser.add_subparsers(title="Sub commands")

        sp = subparsers.add_parser("generate", help="Drop to CLI")
        sp.set_defaults(cmd="generate")
        sp.add_argument(
            "-l",
            "--limit",
            type=int,
            default=0,
            help="Limit number of analyzed packages",
        )
        sp.add_argument(
            "-o", "--outputFile", default="./data.json", help="Data.json file path (defaults to ./data.json)"
        )

        sp = subparsers.add_parser("compare", help="Compare with previous version")
        sp.set_defaults(cmd="compare")

        sp.add_argument(
            "old_data_json_fpath",
            default=None,
            help="Data file to compare it to",
        )
        sp.add_argument(
            "new_data_json_fpath",
            default=None,
            help="Data file to be compared",
        )

        sp = subparsers.add_parser("render", help="Generate Markdown data")
        sp.set_defaults(cmd="render")
        sp.add_argument(
            "data_json_fpath",
            default=None,
            help="Data file to render from",
        )
        sp.add_argument("-o", "--outputDir", default=None, help="Output directory")
        sp.add_argument("-f", "--format", default="md", choices=["md", "adoc"], help="Output format (default Markdown)")

        self.args = parser.parse_args()
        if self.args.verbose > 2:
            self.args.verbose = 2
        log_mappings = {1: logging.INFO, 2: logging.DEBUG}
        logging.basicConfig(
            level=log_mappings.get(self.args.verbose, logging.WARNING),
            format="%(asctime)s |%(levelname)7s|| %(message)s || %(filename)s:%(lineno).d",
        )
        self.l = logging.getLogger(__name__)

    def parse_deb_package_control_file_format(self, fpath=None, f_lines=None):
        if not f_lines:
            if not os.path.exists(fpath):
                return None
            with open(fpath, "r") as f:
                f_lines = f.readlines()

        ret = {}

        def _add_key(k, v):
            if k in ret:
                # the key exists already
                if isinstance(ret[k], list):
                    ret[k].append(v)
                else:
                    ret[k] = list([ret[k]])
                    ret[k].append(v)
            else:
                ret[k] = v

        bad_format = False
        cur_k = ""
        cur_v = ""
        for line in f_lines:
            line: str
            line = line.rstrip()
            if not line:
                continue
            if line[0].isspace():
                if cur_v:
                    cur_v += "\n" + line
                else:
                    cur_v = line
                continue
            if cur_k:
                _add_key(cur_k.strip(), cur_v.strip())
                cur_k = ""
                cur_v = ""

            line = line.strip()
            if not line:
                continue

            items = line.split(":", 1)
            try:
                cur_k = items[0]
                cur_v = items[1]
            except IndexError:
                bad_format = True
                break

        if cur_k:
            _add_key(cur_k.strip(), cur_v.strip())

        if bad_format:
            return None
        return ret

    def get_package_list(self):
        self.packages = list()
        pout = subprocess.check_output(["dpkg", "--get-selections"]).decode()
        count = 0
        for line in pout.split("\n"):
            line: str
            line = line.strip()
            if not line:
                continue
            items = line.split("\t")
            pkg = items[0]

            self.packages.append(pkg)
            count += 1
        self.packages = sorted(self.packages)
        if self.args.limit > 0:
            self.packages = self.packages[: self.args.limit]
            self.l.info(f"Selected {self.args.limit} out of {count}")
        else:
            self.l.info(f"All packages {count=}")
        self.l.debug(f"{self.packages=}")

    def parse_copyright_file(self, cr_fpath):
        if not os.path.exists(cr_fpath):
            return None
        ret = self.parse_deb_package_control_file_format(fpath=cr_fpath)
        if ret:
            mandatory_keys = ["Files"]
            for k in mandatory_keys:
                if k not in ret:
                    msg = f"Could not parse copyright file: no key={k} {cr_fpath=}"
                    self.l.warning(msg)
                    self.discover_issues["warnings"].append(msg)
                    return None

            ret["_license_names"] = []

            # preprocess files and _license_names
            def _preprocess_license_name(lic_text: str):
                if "\n" not in lic_text:
                    return lic_text
                return lic_text.split("\n")[0].strip()

            def _add_license_name(lic_name):
                lic_name = _preprocess_license_name(lic_name)
                if lic_name not in ret["_license_names"]:  # de-duplicate names
                    ret["_license_names"].append(lic_name)

            c_files = ret.get("Files")
            if isinstance(c_files, list):
                for ndx, c_file in enumerate(c_files):
                    available_licenses = ret.get("License", [])
                    if ndx >= len(available_licenses):
                        self.l.warning(f"Cannot find license name for {c_file=} in {cr_fpath}")
                        continue
                    _add_license_name(available_licenses[ndx].strip())
            else:
                c_lic = ret.get("License")

                if isinstance(c_lic, list):
                    _add_license_name(c_lic[0])
                else:
                    _add_license_name(c_lic)
        return ret

    def get_packages_info(self):
        if not self.packages:
            return
        self.packages_info = OrderedDict()
        for ndx, p in enumerate(self.packages):
            p_info = {}
            pout = subprocess.check_output(["dpkg", "-s", p]).decode().strip()

            p_info = self.parse_deb_package_control_file_format(f_lines=pout.split("\n"))
            new_p = p_info.get("Package")
            if not new_p:
                self.l.warning(f"could not parse dpkg -s for {p=} {p_info=}")
                continue
            p_info["_copyright_fpath"] = f"/usr/share/doc/{new_p}/copyright"
            p_info["_copyright_info"] = self.parse_copyright_file(p_info["_copyright_fpath"])

            self.packages_info[new_p] = p_info
        self.l.debug(f"{self.packages_info=}")

    def get_os_info(self):
        os_release_path = "/etc/os-release"
        self.os_release_info = None
        if os.path.exists(os_release_path):
            with open(os_release_path) as f:
                self.os_release_info = f.read()

    def save_data(self):
        out_dir = os.path.dirname(self.args.outputFile)
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)

        with open(self.args.outputFile, "w+") as f:
            json.dump(
                {
                    "generate_ts": utc_now(),
                    "package_info": self.packages_info,
                    "os_release": self.os_release_info,
                    "format_version": self.data_format_version,
                    "issues": self.discover_issues,
                    "args": vars(self.args),
                },
                f,
                indent=4,
            )

    def generate_compare_data(self):
        with open(self.args.old_data_json_fpath) as f:
            old_data = json.load(f)
        old_pkg_info = old_data.get("package_info")
        with open(self.args.new_data_json_fpath) as f:
            new_data = json.load(f)
        new_pkg_info = new_data.get("package_info")
        new_packages = []
        deleted_packages = []
        changed_packages = []
        for p, pinfo in old_pkg_info.items():
            if p not in new_pkg_info:
                deleted_packages.append(pinfo.get("Package"))
        for p, pinfo in new_pkg_info.items():
            if p not in old_pkg_info:
                new_packages.append(pinfo.get("Package"))
                continue
            if pinfo["Version"] != old_pkg_info[p]["Version"]:
                changed_packages.append(old_pkg_info[p])
        new_data["compare_info"] = {
            "compare_ts": utc_now(),
            "compared_with_path": self.args.old_data_json_fpath,
            "new_packages": new_packages,
            "deleted_packages": deleted_packages,
            "changed_packages": changed_packages,
        }
        with open(self.args.new_data_json_fpath, "w") as f:
            json.dump(new_data, f, indent=4)

    def render_md_pck_info(self, data):
        packages = data.get("package_info")
        os_release_info = data.get("os_release")
        with open(os.path.join(self.out_dir, "summary.md"), "w+") as f:
            with open(os.path.join(self.out_dir, "all_license_files.md"), "w+") as f_a:
                if os_release_info:
                    f.write("# OS-release info\n\n" "```\n" + os_release_info + "```\n\n")
                f.write(
                    "# OSDF packages info summary\n\n"
                    "| Nr | Package | Version | License |\n"
                    "| --- | --------------- | ------------------ | -------------- |\n"
                )

                f_a.write("# OSDF license files\n\n")
                count = 0
                for p, pinfo in packages.items():
                    count += 1
                    lic = f"custom license file"
                    if pinfo["_copyright_info"]:
                        lic = "; ".join(sorted(pinfo["_copyright_info"]["_license_names"]))

                    f.write(f"| {count} |{pinfo['Package']} | {pinfo['Version']} | {lic} |\n")
                    # all licenses file

                    f_a.write(
                        f"## {count}. Package: {pinfo['Package']} \n\n"
                        "### Summary\n\n"
                        "| Package | Version | Source | License |\n"
                        "| --------------- | ------------------ | -------------- | -------------- |\n"
                        f"| {pinfo['Package']} | {pinfo['Version']} | {pinfo['_copyright_fpath']} | {lic} |\n\n"
                    )
                    if os.path.exists(pinfo["_copyright_fpath"]):
                        with open(pinfo["_copyright_fpath"], "r") as l_f:
                            f_a.write(f"### License file content\n\n```\n{l_f.read()}\n```\n\n")
                    else:
                        f_a.write("No license file present\n\n")

    def render_md_compare_info(self, data):
        packages = data.get("package_info")
        compare_info = data.get("compare_info")
        if compare_info is None:
            return
        new_packages = compare_info.get("new_packages")
        deleted_packages = compare_info.get("deleted_packages")
        changed_packages = compare_info.get("changed_packages")
        dst_fpath = os.path.join(self.out_dir, "compare.md")
        with open(dst_fpath, "w+") as f:
            f.write(
                f"## Comparin with {compare_info.get('compared_with_path')!r}\n\n"
                "### New Packages\n\n"
                f"There are {len(new_packages)} new packages.\n\n"
            )
            if len(new_packages):
                f.write("| NR | Package |\n| --------------- | ------------------ |\n")
            for ndx, pname in enumerate(new_packages, 1):
                f.write(f"| {ndx} | {pname} | \n")
            f.write("\n")

            f.write("### Deleted Packages\n\n" f"There are {len(deleted_packages)} deleted packages.\n\n")
            if len(deleted_packages):
                f.write("| NR | Package |\n| --------------- | ------------------ |\n")
            for ndx, pname in enumerate(deleted_packages, 1):
                f.write(f"| {ndx} | {pname} | \n")
            f.write("\n")

            f.write("### Changed Packages\n\n" f"There are {len(changed_packages)} changed packages.\n\n")
            if len(changed_packages):
                f.write(
                    "| NR | Package | Old Version | New Version |\n"
                    "| --------------- | ------------------ | ------------------ | ------------------ |\n"
                )
            for ndx, old_pinfo in enumerate(changed_packages, 1):
                pname = old_pinfo.get("Package")
                pinfo = packages.get(pname)
                f.write(f"| {ndx} | {pname} | {old_pinfo.get('Version')} | {pinfo.get('Version')} |\n")
            f.write("\n")

    def render_adoc_pck_info(self, data):
        packages = data.get("package_info")
        os_release_info = data.get("os_release")
        with open(os.path.join(self.out_dir, "summary.adoc"), "w+") as f:
            with open(os.path.join(self.out_dir, "all_license_files.adoc"), "w+") as f_a:
                if os_release_info:
                    f.write("== OS-release info\n\n[literal]\n" + os_release_info + "\n\n")
                f.write('== OSDF packages info summary\n\n[cols="1,4,4,4"]\n|===\n|Nr |Package |Version |License\n\n')

                f_a.write("== OSDF license files\n\n")
                count = 0
                for p, pinfo in packages.items():
                    count += 1
                    lic = f"custom license file"
                    if pinfo["_copyright_info"]:
                        lic = "; ".join(sorted(pinfo["_copyright_info"]["_license_names"]))

                    f.write(f"|{count}\n|{pinfo['Package']}\n|{pinfo['Version']}\n|{lic}\n")
                    # all licenses file

                    f_a.write(
                        f"=== {count}. Package: {pinfo['Package']} \n\n"
                        "==== Summary\n\n"
                        '[cols="1,1,1,1"]\n|===\n|Package |Version |Source |License \n\n'
                        f"|{pinfo['Package']}\n|{pinfo['Version']}\n|{pinfo['_copyright_fpath']}\n|{lic}\n|===\n\n"
                    )
                    if os.path.exists(pinfo["_copyright_fpath"]):
                        with open(pinfo["_copyright_fpath"], "r") as l_f:
                            f_a.write(f"==== License file content\n\n....\n{l_f.read()}\n....\n\n")
                    else:
                        f_a.write("No license file present\n\n")
                f.write("\n")
                f_a.write("\n")

    def render_adoc_compare_info(self, data):
        packages = data.get("package_info")
        compare_info = data.get("compare_info")
        if compare_info is None:
            return
        new_packages = compare_info.get("new_packages")
        deleted_packages = compare_info.get("deleted_packages")
        changed_packages = compare_info.get("changed_packages")
        dst_fpath = os.path.join(self.out_dir, "compare.md")
        with open(dst_fpath, "w+") as f:
            f.write(
                f"=== Comparin with {compare_info.get('compared_with_path')!r}\n\n"
                "==== New Packages\n\n"
                f"There are {len(new_packages)} new packages.\n\n"
            )
            if len(new_packages):
                f.write('[cols="1,1"]\n|===\n|NR | Package\n\n')
            for ndx, pname in enumerate(new_packages, 1):
                f.write(f"|{ndx}\n|{pname}\n")
            f.write("|===\n\n")

            f.write("==== Deleted Packages\n\n" f"There are {len(deleted_packages)} deleted packages.\n\n")
            if len(deleted_packages):
                f.write('[cols="1,1"]\n|===\n|NR | Package\n\n')
            for ndx, pname in enumerate(deleted_packages, 1):
                f.write(f"|{ndx}\n|{pname}\n")
            f.write("|===\n\n")

            f.write("==== Changed Packages\n\n" f"There are {len(changed_packages)} changed packages.\n\n")
            if len(changed_packages):
                f.write('[cols="1,1,1,1"]\n|===\n|NR |Package |Old Version |New Version\n\n')
            for ndx, old_pinfo in enumerate(changed_packages, 1):
                pname = old_pinfo.get("Package")
                pinfo = packages.get(pname)
                f.write(f"|{ndx}\n|{pname}\n|{old_pinfo.get('Version')}\n|{pinfo.get('Version')}\n")
            f.write("|===\n\n")

    def _cmd_compare(self):
        self.generate_compare_data()

    def _cmd_generate(self):
        self.get_package_list()
        self.get_packages_info()
        self.get_os_info()
        self.save_data()

    def _cmd_render(self):
        self.out_dir = self.args.outputDir
        if self.out_dir is None:
            self.out_dir = os.path.dirname(self.args.data_json_fpath)
        if not os.path.exists(self.out_dir):
            os.makedirs(self.out_dir)

        with open(self.args.data_json_fpath) as f:
            data = json.load(f)
        if self.args.format == "md":
            self.render_md_pck_info(data)
            self.render_md_compare_info(data)
        else:
            self.render_adoc_pck_info(data)
            self.render_adoc_compare_info(data)

    def start(self):
        self.args_parse()
        method_name = f"_cmd_{self.args.cmd}"
        try:
            method = self.__getattribute__(method_name)
        except AttributeError:
            self.l.error("Unknown CMD=%r", self.args.cmd)
            return
        try:
            # call the message handler
            method()
        except Exception:
            self.l.exception("Unhandled exception while handling cmd=%r", self.args.cmd)


if __name__ == "__main__":
    a = App()
    a.start()
