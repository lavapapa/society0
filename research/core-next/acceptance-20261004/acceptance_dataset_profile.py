import cProfile,pstats,io
from pathlib import Path
from benchmarks.core_next_dataset_access import probe
p=cProfile.Profile();p.enable();result=probe(Path('/tmp/society0-acceptance-20261004/performance-new/run'),pages=100,limit=10,random_reads=100);p.disable();p.dump_stats('/mnt/data/l20/qin/society0-acceptance-20261004/dataset-profile.pstats');pstats.Stats(p).strip_dirs().sort_stats('cumulative').print_stats(22)
