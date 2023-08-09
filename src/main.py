#!/usr/bin/env python3
"""
Tool to quickly convert from JSON to YAML and vice-versa. 
"""

import os
import argparse
import logging
import subprocess


class UnsupportedConfigFileExtension(Exception):
    pass


class App:
    def __init__(self) -> None:
        self.args = None
        self.l = None
        self.packages = None
        self.packages_info = None

    def args_parse(self):
        parser = argparse.ArgumentParser(description="convert to json")
        parser.add_argument(
            "-v",
            "--verbose",
            action="count",
            default=0,
            help="Enable debugging (count up to 3 levels)",
        )
        parser.add_argument(
            "-l",
            "--limit",
            type=int,
            default=0,
            help="Limit number of analyzed packages",
        )
        parser.add_argument("-o", "--output", default=None, help="Output file name")

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
            if self.args.limit > 0 and count >= self.args.limit:
                break
        self.packages = sorted(self.packages)
        self.l.debug(f"{self.packages=}")

    def parse_copyright_file(self, cr_fpath):
        if not os.path.exists(cr_fpath):
            return None
        ret = self.parse_deb_package_control_file_format(fpath=cr_fpath)
        if ret:
            mandatory_keys = ["Files"]
            for k in mandatory_keys:
                if k not in ret:
                    self.l.warning(f"Could not parse copyright file: no key={k} {cr_fpath=}")
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
        self.packages_info = {}
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

    def generate_summary_file(self):
        with open("./summary.md", "w+") as f:
            with open("all_license_files.md", "w+") as f_a:
                f.write(
                    "# OSDF packages info summary\n\n"
                    "| Nr | Package | Version | License |\n"
                    "| --- | --------------- | ------------------ | -------------- |\n"
                )

                f_a.write("# OSDF license files\n\n")
                count = 0
                for p, pinfo in self.packages_info.items():
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

    def start(self):
        self.args_parse()
        self.get_package_list()
        self.get_packages_info()
        self.generate_summary_file()


if __name__ == "__main__":
    a = App()
    a.start()
