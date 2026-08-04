from sys import argv
from load_source import load_bench
from detyper import detype

source = load_bench(argv[1], argv[2])
do_stage_two = eval(argv[3]) if len(argv) > 3 else True
if len(argv) >= 4:
    print(detype(source, do_stage_two, mask=eval(argv[4])))
else:
    print(detype(source, do_stage_two))

