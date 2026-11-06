#!/usr/bin/env python3
"""Replacement helper copied into a scratch scripts tree by the offline suite."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import time
p=argparse.ArgumentParser();p.add_argument('pdf');p.add_argument('output',nargs='?');p.add_argument('--mode',choices=['extract','convert'],default='extract');p.add_argument('--max-attempts',type=int,default=3);p.add_argument('--verbose',action='store_true');a=p.parse_args()
behaviour=os.environ.get('FAKE_MATHPIX_BEHAVIOUR','ok')
info=subprocess.check_output(['pdfinfo',a.pdf]).decode();pages=int(re.search(r'^Pages:\s+(\d+)',info,re.M)[1])
ledger=os.environ.get('FAKE_MATHPIX_LEDGER')
if ledger:
    with open(ledger,'a') as f:f.write(f'{int(time.time())}\t{pages}\t{Path(a.pdf).name}\t{a.max_attempts}\t{behaviour}\n')
if behaviour.startswith('sleep:'):time.sleep(float(behaviour.split(':')[1]))
if behaviour=='fail':sys.exit(1)
text='' if behaviour=='empty' else subprocess.check_output(['pdftotext',a.pdf,'-']).decode()
if text:text='$$\n$$\n'+text+'\nMATHPIX-'+Path(a.pdf).name+'\n'
if a.mode=='extract':sys.stdout.write(text)
else:
    out=Path(a.output) if a.output else Path(a.pdf).with_suffix('.md');out.write_text(text);print(out)
