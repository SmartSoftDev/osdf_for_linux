. $(gbl log)

gblcmd_generate_compare_render(){
    local start_date="`date '+%Y-%m-%d_%Hh_%Mm_%Ss'`"
    ../src/main.py generate -o new_osdf_data/data.json -l 50 $@ || fatal "Could not generate OSDF in $out_dir"
    ../src/main.py compare old_osdf_data/data.json new_osdf_data/data.json   $@ || fatal "Could not generate OSDF in $out_dir"
    ../src/main.py render new_osdf_data/data.json   $@ || fatal "Could not generate OSDF in $out_dir"
}