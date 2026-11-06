#!/usr/bin/env python3
"""Synthetic offline agy protocol double; never delegates to another binary."""
import atexit
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

HERE=Path(__file__).parent
ARGS=sys.argv[1:]
DENIED='jetski: no output produced — a tool required the "command" permission that headless mode cannot prompt for, so it was auto-denied. This is a headless session.'

def sample(name): return json.loads((HERE/'samples'/name).read_text())
def emit(text, structured=None, denied=False):
    if fmt=='json':
        obj=sample('agy_json_schema_envelope.sample.json' if schema else 'agy_json_envelope.sample.json')
        obj['response']=text
        if schema:
            obj['structured_output']=structured if structured is not None else {}
        else:
            obj.pop('structured_output',None);obj.pop('json_schema',None)
        if denied: obj['denied_actions']=[{'action':'command','display_name':'RunCommand'}]
        print(json.dumps(obj,ensure_ascii=False))
    else:
        if text: print(text)

if '--help' in ARGS:
    print('--print, -p PROMPT\n--print-timeout DURATION\n--add-dir DIR\n--output-format text|json|stream-json\n--json-schema JSON\n--dangerously-skip-permissions\n--effort low|medium|high\n--version'+('' if os.environ.get('FAKE_AGY_HELP')=='nomodel' else '\n--model ID'))
    sys.exit(0)
if '--version' in ARGS: print('1.1.26-fake');sys.exit(0)
if ARGS==['models']:
    print('gemini-4-argon-high\tGemini 4 Argon (High)\ngemini-3.8-flash-high\tGemini 3.8 Flash (High)');sys.exit(0)
model='gemini-3.8-flash-high';has_model=0;prompt='';fmt='text';schema=False;add_dir=None
for i,arg in enumerate(ARGS):
    if arg=='--model': model=ARGS[i+1];has_model=1
    elif arg in ('-p','--print'): prompt=ARGS[i+1]
    elif arg=='--output-format': fmt=ARGS[i+1]
    elif arg=='--json-schema': schema=True
    elif arg=='--add-dir': add_dir=Path(ARGS[i+1])
role='pro' if ('pro' in model.lower() or 'argon' in model.lower()) else 'flash'
if 'bogus' in model or 'does-not-exist' in model:
    obj=sample('agy_bogus_model_error.sample.json');print(json.dumps(obj));sys.exit(1)
if prompt=='/model':
    if os.environ.get('FAKE_AGY_MODEL_PROBE_FAIL')==role: sys.exit(1)
    report=os.environ.get('FAKE_AGY_MODEL_REPORT',model)
    obj=sample('agy_model_probe_'+role+'.sample.json')
    ident=report
    if report.startswith('Gemini '):
        ident='gemini-4-argon-high' if 'Argon' in report else ('gemini-3.1-pro-high' if 'Pro' in report else 'gemini-3.8-flash-high')
    label='Gemini 4 Argon (High)' if 'argon' in ident else ('Gemini 3.1 Pro (High)' if 'pro' in ident else 'Gemini 3.8 Flash (High)')
    obj['command']['data']['id']=ident;obj['command']['data']['label']=label
    obj['response']=ident+'\t'+label+'\n'
    print(json.dumps(obj) if fmt=='json' else obj['response'],end='\n');sys.exit(0)
if 'AUDIT TASK' in prompt:kind='audit'
elif 'REPAIR TASK' in prompt:kind='repair'
elif 'Mathpix OCR' in prompt:kind='hybrid'
elif any(token in prompt for token in ('Convert its ENTIRE content','continuing that conversion','Convert the following academic paper text','This is a CONTINUATION of an academic paper')):kind='convert'
else:
    print('fake agy: unrecognized prompt; required task token missing',file=sys.stderr)
    sys.exit(1)
m=re.search(r'pages\s+(\d+)[-–](\d+)',prompt,re.I)
a,b=(int(m[1]),int(m[2])) if m else (1,25)
page_range=f'{a}-{b}'
# Read once so the ledger observes the actual stdin bytes, including in dir mode.
stdin_data=sys.stdin.buffer.read()
stdin_text=stdin_data.decode()
add_dir_entries=list(add_dir.iterdir()) if add_dir is not None else []
add_dir_pdfs=[p for p in add_dir_entries if p.is_file() and p.suffix.lower()=='.pdf']
pdfs=list(Path.cwd().glob('*.pdf'))
marker_pdfs=add_dir_pdfs if add_dir is not None else pdfs
stdin_marker=int(any('MATHPIX-'+pdf.name in stdin_text for pdf in marker_pdfs))
ledger=Path(os.environ['FAKE_AGY_LEDGER']);ledger.parent.mkdir(parents=True,exist_ok=True)
prior=[l.split('\t') for l in ledger.read_text().splitlines()] if ledger.exists() else []
same_calls=[r for r in prior if len(r)>5 and r[3]==kind and r[4]==page_range]
dispatch=1+len(same_calls)
prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()[:8]
# Scenario selectors remain dispatch ordinals; the ledger's attempt identifies
# a caller attempt shared by the two identical transient transport calls.
transient_retry=bool(prior and same_calls and prior[-1]==same_calls[-1] and
                     prior[-1][6]=='neterr' and prior[-1][9]==prompt_hash and
                     (len(prior[-1])<19 or prior[-1][18]=='0'))
attempt=(int(same_calls[-1][5])+int(not transient_retry)) if same_calls else 1
behaviour='pass' if kind=='audit' else 'ok'
scenario=os.environ.get('FAKE_AGY_SCENARIO')
if scenario and Path(scenario).exists():
    for line in Path(scenario).read_text().splitlines():
        if line.startswith('#') or not line.strip(): continue
        row=line.split('\t')
        if len(row)>=4 and row[0] in (kind,'*') and row[1] in (page_range,'*') and row[2] in (str(dispatch),'*'):
            behaviour=row[3];break
if os.environ.get('FAKE_AGY_DENY') in (kind,'all'): behaviour='deny'
overlap_dir=Path(os.environ.get('FAKE_AGY_OVERLAP_DIR',str(ledger)+'.active'));overlap_dir.mkdir(parents=True,exist_ok=True)
overlap=0
for marker in overlap_dir.iterdir():
    try: os.kill(int(marker.name),0);overlap=1
    except (OSError,ValueError): pass
marker=overlap_dir/str(os.getpid());marker.touch();atexit.register(lambda:marker.unlink(missing_ok=True))
row=[str(int(time.time()*1000)),str(os.getpid()),role,kind,page_range,str(attempt),behaviour,str(has_model),str(overlap),prompt_hash,hashlib.sha256('\0'.join(ARGS).encode()).hexdigest()[:8],model,
     str(int(add_dir is not None)),str(len(add_dir_entries)),str(int(len(add_dir_pdfs)==1)),str(len(stdin_data)),str(stdin_marker),str(dispatch),str(int(transient_retry))]
with ledger.open('a') as f:f.write('\t'.join(row)+'\n')
if os.environ.get('FAKE_AGY_PROMPT_DIR'):
    dest=Path(os.environ['FAKE_AGY_PROMPT_DIR']);dest.mkdir(parents=True,exist_ok=True);(dest/f'{kind}-{page_range}-{attempt}.txt').write_text(prompt)
if behaviour.startswith('sleep:'):
    time.sleep(float(behaviour.split(':')[1]));behaviour='pass' if kind=='audit' else 'ok'
if behaviour=='hang':
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    child=subprocess.Popen(['sleep','600'])
    with Path(str(ledger)+'.children').open('a') as f:f.write(str(child.pid)+'\n')
    child.wait();sys.exit(0)
if behaviour=='exit1':sys.exit(1)
if behaviour=='neterr':
    print('Error: Eligibility check failed: dial tcp: lookup fake.agy.invalid: no such host\nThere was a network issue connecting to the server',file=sys.stderr)
    sys.exit(1)
if behaviour=='empty':emit('');sys.exit(0)
if behaviour=='deny':print(DENIED,file=sys.stderr);emit('',denied=True);sys.exit(0)
if kind=='audit':
    if behaviour=='garbage':emit('This is not a JSON audit.');sys.exit(0)
    obj={'schema':'gemini-pdf-audit/1','pages':[{'page':p,'readable':True,'verified':True,'note':''} for p in range(a,b+1)],'seam_checked':a>1 and 'BEGIN PRIOR-OUTPUT CONTEXT' in prompt,'findings':[],'truncated':False,'verdict':'PASS'}
    if behaviour.startswith('fail:'):
        page=a;detail=behaviour[5:]
        if behaviour.startswith('fail:page-missing:'):
            page=int(behaviour.rsplit(':',1)[1])
            if not a<=page<=b:raise RuntimeError('missing-page audit is outside the chunk range')
            detail=f'The entire page {page} is missing from the candidate.'
        obj['findings']=[{'kind':'MISSING_TEXT','severity':'critical','page':page,'anchor':'','pdf_text':'','candidate_text':'','detail':detail}];obj['verdict']='FAIL'
    nonce=re.search(r'AUDIT-JSON-BEGIN\s+([^\s>]+)',prompt)
    nonce=nonce[1] if nonce else 'MISSING-NONCE'
    emit(f'<!-- AUDIT-JSON-BEGIN {nonce} -->\n'+json.dumps(obj,ensure_ascii=False)+f'\n<!-- AUDIT-JSON-END {nonce} -->',obj);sys.exit(0)

if pdfs and kind!='hybrid':
    text=subprocess.check_output(['pdftotext','-layout',str(pdfs[0]),'-']).decode()
else:
    text=stdin_text
    # Hybrid stdin can also ride in a delimited prompt in the audited path.
    if not text:
        block=re.search(r'--- BEGIN (?:MATHPIX[^\n]*)---\n(.*?)\n--- END',prompt,re.S)
        text=block[1] if block else ''
if behaviour.startswith('ok_drop_page:'):
    page=int(behaviour.rsplit(':',1)[1]);pieces=text.split('\f')
    if not a<=page<=b or len(pieces)<b-a+1:
        raise RuntimeError('page-deletion fixture needs one extracted page per chunk page')
    pieces[page-a]='';text='\f'.join(pieces)
text=text.replace('\f','\n')
text=re.sub(r'(?m)^\s*\d{1,2}\s*$','',text)
text=re.sub(r'(?m)^\s*(\d+(?:\.\d+)?)\s+([A-Z][^\n]{0,60})$',lambda m: ('### ' if '.' in m[1] else '## ')+m[1]+' '+m[2].strip(),text)
text=re.sub(r'(?m)^([^\n]*?)[ \t]{2,}\((\d+)\)\s*$',lambda m:'$$ '+m[1].strip()+r' \tag{'+m[2]+'} $$',text)
text=re.sub(r'(\w)-\n\s*(\w)',r'\1\2',text)
if a==1 and not text.lstrip().startswith('---'):
    text='---\ntitle: Synthetic Econometric Pipeline Paper\nauthors: Offline Fixture Author\nyear: 2026\n---\n# Synthetic Econometric Pipeline Paper\n'+text
if behaviour=='ok_drop_numbers':
    count=[0]
    def drop(m):
        count[0]+=1
        return '' if count[0]%5<3 else m[0]
    text=re.sub(r'\b\d{3,}(?:\.\d+)?\b',drop,text)
if behaviour=='ok_drop_half':text='\n'.join(text.splitlines()[::2])
if behaviour=='ok_drop_paragraph':
    blocks=list(re.finditer(r'(?ms)(?:^|\n\s*\n)([^\n]*(?:\n(?!\s*\n)[^\n]*)+)',text))
    blocks=[m for m in blocks if len(m[1])>=400 and len(re.findall(r'[A-Za-z]+',m[1]))>=50 and '$$' not in m[1]]
    if not blocks: raise RuntimeError('fixture has no long prose paragraph to delete')
    target=min(blocks,key=lambda m:abs(m.start()-len(text)//2));text=text[:target.start(1)]+text[target.end(1):]
if behaviour=='ok_inject_doi':text+='\nFabricated reference DOI: 10.12345/fabricated-reference\n'
if kind=='repair':text+=f'\nREPAIRED-{a}-{b}\n'
if kind=='hybrid' and 'MATHPIX-' not in text:
    # A missing OCR input must fail the test instead of being reconstructed
    # independently from the PDF and masking a broken stdin connection.
    print('fake hybrid received no Mathpix OCR marker',file=sys.stderr)
    sys.exit(1)
sentinel=re.search(r'<!-- END OF CHUNK [^>]*-->',prompt)
sentinel=sentinel[0] if sentinel else '<!-- END OF CHUNK MISSING -->'
if behaviour=='truncate':emit(text[:int(len(text)*.4)]);sys.exit(0)
if behaviour=='truncate_keep_sentinel':text=text[:int(len(text)*.55)]
if behaviour!='nosentinel':text+='\n'+sentinel
if behaviour=='dupsentinel':text+='\n'+sentinel
emit(text)
