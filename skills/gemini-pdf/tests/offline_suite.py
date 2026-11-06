#!/usr/bin/env python3
"""Synthetic offline scenarios. Failures retain scratch evidence."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

TESTS=Path(__file__).resolve().parent
SOURCE=TESTS.parent/'scripts'
NAMES='a b b2 b3 b4 b5 c d d2 e f f2 f3 f4 f5 g g2 g3 h k2 l m n n2 o p q r s1 t u v v2 w T12 T13 T16 T17 T18 T19 T21'.split()
RUN=None
SCRIPT=None
FIXTURE=None
REAL_JQ=None
AGY_COLUMNS='timestamp pid role kind range attempt behaviour has_model overlap prompt_hash args_hash model has_add_dir add_dir_files add_dir_pdf stdin_bytes stdin_marker dispatch transient_retry'.split()
AUDIT_SUMMARY_RE=re.compile(r'^\[gemini-pdf\] AUDIT: status=(verified|degraded|partial|failed|disabled) (chunks=\d+ verified=\d+ unaudited=\d+ unverified=\d+ failed=\d+ .*|reason=\S+ chunks=\d+)$')
AUDIT_SUMMARY_KEYS='status chunks verified unaudited unverified failed repaired mathpix_chunks mathpix_pages cap_exceeded mathpix_usd deadline residual contradicted skipped external elapsed convert_model audit_model'.split()


def check(condition,message):
    if not condition:raise AssertionError(message)
def read_json(path):return json.loads(Path(path).read_text())
def table(path):return [l.split('\t') for l in Path(path).read_text().splitlines() if l] if Path(path).exists() else []
def ledger(path):
    rows=table(path)
    return [dict(zip(rows[0],r)) for r in rows[1:]] if rows else []

def run_proc(argv,env,cwd=None,limit=85):
    p=subprocess.Popen(argv,env=env,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    try:out,err=p.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid,signal.SIGKILL);out,err=p.communicate();raise AssertionError('test watchdog expired: '+str(argv[0]))
    return p.returncode,out,err

class Case:
    def __init__(self,name,rows='',env=None):
        self.name=name;self.root=RUN/name;self.root.mkdir(parents=True,exist_ok=True)
        self.pdf=self.root/'synthetic_paper.pdf';shutil.copyfile(FIXTURE,self.pdf)
        self.out=self.root/'out.md';self.scenario=self.root/'scenario.tsv'
        catalog=TESTS/'scenarios'/f'{name}.tsv'
        if catalog.exists():
            stored=''.join(line+'\n' for line in catalog.read_text().splitlines() if line and not line.startswith('#'))
            check(stored==rows,'scenario catalogue drift for '+name)
            shutil.copyfile(catalog,self.scenario)
        else:self.scenario.write_text(rows)
        (self.root/'ref/md').mkdir(parents=True,exist_ok=True)
        self.env=dict(os.environ)
        for k in list(self.env):
            if k.startswith(('GEMINI_PDF_','FAKE_AGY_','FAKE_MATHPIX_','FAKE_JQ_')) and not k.startswith('GEMINI_PDF_OFFLINE_'):self.env.pop(k)
        self.env.update({
            'PATH':str(RUN/'fakes')+os.pathsep+os.environ['PATH'],'PYTHONDONTWRITEBYTECODE':'1',
            'TMPDIR':str(self.root),
            'GEMINI_PDF_MODEL':'gemini-4-argon-high','GEMINI_PDF_AUDIT_MODEL':'gemini-3.8-flash-high',
            'GEMINI_PDF_TIMEOUT':'6','GEMINI_PDF_WATCHDOG_GRACE':'2','GEMINI_PDF_LOCK_WAIT_MAX':'60',
            'GEMINI_PDF_LOCK_DIR':str(self.root/'lock'),'GEMINI_PDF_CONVERT_MIN':'2',
            'GEMINI_PDF_AUDIT_MIN':'2','GEMINI_PDF_REPAIR_MIN':'2','GEMINI_PDF_MATHPIX_MIN':'2',
            'GEMINI_PDF_PUBLISH_RESERVE':'1','GEMINI_PDF_LOCAL_STEP_TIMEOUT':'30',
            'GEMINI_PDF_MATHPIX_PAGE_CAP':'20','GEMINI_PDF_MATHPIX_LEDGER':str(self.root/'mathpix_ledger.tsv'),
            'REFERENCE_DIR':str(self.root/'ref'),'MATHPIX_APP_ID':'fake','MATHPIX_APP_KEY':'fake',
            'FAKE_AGY_LEDGER':str(self.root/'agy.tsv'),'FAKE_AGY_SCENARIO':str(self.scenario),
            'FAKE_AGY_PROMPT_DIR':str(self.root/'prompts'),'FAKE_AGY_OVERLAP_DIR':str(self.root/'overlap'),
            'FAKE_MATHPIX_LEDGER':str(self.root/'mathpix.tsv'),
            'FAKE_REAL_JQ':REAL_JQ,
        })
        if env:self.env.update({k:str(v) for k,v in env.items()})
        self.audit={};self.rc=None;self.stdout='';self.stderr=''
    def run(self,*flags,script=None,limit=85):
        self.started=time.monotonic()
        self.rc,self.stdout,self.stderr=run_proc(['bash',str(script or SCRIPT),str(self.pdf),str(self.out),*flags],self.env,limit=limit)
        self.elapsed=time.monotonic()-self.started
        (self.root/'stdout').write_text(self.stdout);(self.root/'stderr').write_text(self.stderr);(self.root/'rc').write_text(str(self.rc))
        if self.out.with_suffix('.audit.json').exists():self.audit=read_json(self.out.with_suffix('.audit.json'))
        return self
    def rows(self,kind=None,ran=None):
        return [r for r in table(self.root/'agy.tsv') if (kind is None or r[3]==kind) and (ran is None or r[4]==ran)]
    def agy_calls(self,kind=None,ran=None):return [dict(zip(AGY_COLUMNS,r)) for r in self.rows(kind,ran)]
    def mathpix(self):return table(self.root/'mathpix.tsv')
    def ledger(self):return ledger(self.env['GEMINI_PDF_MATHPIX_LEDGER'])
    def expect(self,status,rc=0):
        check(self.rc==rc,f'rc {self.rc}, expected {rc}; log {self.root}/stderr')
        check(self.audit.get('status')==status,f'status {self.audit.get("status")}, expected {status}; report {self.root}/out.audit.json')
        return self
    def common(self,notices=None):
        actual=self.out if self.out.exists() else self.out.with_suffix('.failed.md')
        if actual.exists():
            check('END OF CHUNK' not in actual.read_text(),'conversion sentinel leaked into Markdown')
            check('AUDIT-JSON-' not in actual.read_text(),'audit nonce leaked into Markdown')
            check(self.stdout==str(actual)+'\n','stdout must contain only the output path')
        if notices is None:notices=int(bool(self.rows()))+int(bool(self.mathpix()))
        check(self.stderr.count('CONFIDENTIALITY:')==notices,f'CONFIDENTIALITY count {self.stderr.count("CONFIDENTIALITY:")}, expected {notices}')
        line=[l for l in self.stderr.splitlines() if l.startswith('[gemini-pdf] AUDIT:')]
        if self.audit or actual.exists() or line:
            check(len(line)==1,'expected exactly one AUDIT summary')
            check(self.stderr.splitlines()[-1]==line[0],'AUDIT summary must be last stderr line')
            check(AUDIT_SUMMARY_RE.fullmatch(line[0]) is not None,'AUDIT summary dropped the contract vocabulary: '+line[0])
            if not line[0].startswith('[gemini-pdf] AUDIT: status=disabled reason='):
                keys=[word.split('=',1)[0] for word in line[0].split()[2:]]
                check(all(keys.count(key)==1 for key in AUDIT_SUMMARY_KEYS),'AUDIT summary must contain every fixed key exactly once')
        if self.audit:
            stored=self.audit['summary_line']
            check(stored==line[0] or '[gemini-pdf] '+stored==line[0],'summary_line differs from stderr')
            if self.audit['status']!='disabled':
                totals=self.audit['totals']
                check(sum(totals[x] for x in ('verified','unaudited','unverified','failed'))==totals['chunks'],'chunk status invariant')
                check(self.audit['schema']==1,'report schema must be 1')
                for key in ('input','models','deadline','mathpix','chunks','skipped_steps','degradations','residual'):check(key in self.audit,'missing report key '+key)
        for row in self.rows():
            check(row[7]=='1','external agy call omitted --model')
            check(row[8]=='0','overlapping agy calls')
            check(row[11]==('gemini-3.8-flash-high' if row[3]=='audit' else 'gemini-4-argon-high'),'incorrect model routing')
        for row in self.mathpix():check(row[3]=='1','guard did not pass --max-attempts 1')
        return self
    def prompt(self,kind,ran,attempt=1):return (self.root/'prompts'/f'{kind}-{ran}-{attempt}.txt').read_text()


def rows(*items):return ''.join('\t'.join(map(str,r))+'\n' for r in items)
FAIL_LAST=rows(('audit','21-25',1,'fail:persistent issue'),('audit','21-25',2,'fail:persistent issue'))
GARBAGE=rows(('audit','11-20',1,'garbage'),('audit','11-20',2,'garbage'))

def check_final_verified(c):
    c.expect('verified').common()
    check(c.audit['totals']['chunks']==3 and c.audit['totals']['verified']==3,'all 3 chunks must verify')

def a():
    c=Case('a',env={'GEMINI_PDF_KEEP_AUDIT_DIR':1})
    previous=b'previous trustworthy verified content\r\n\x00preserve these exact bytes\n'
    c.out.write_bytes(previous);c.run();check_final_verified(c);final_artifacts(c,previous)
    check(c.out.read_bytes()!=previous,'verified run did not replace the previous output')
    check(len(c.rows('convert'))==3 and len(c.rows('audit'))==3 and not c.rows('repair'),'wrong call counts')
    check(not c.mathpix(),'unexpected Mathpix dispatch')
    check((c.root/'ref/md/out.md').exists(),'verified output not synced')
    for suffix in ('.err','.meta.json','.quality.json'):check(c.out.with_suffix(suffix).exists(),'missing '+suffix)
    check(c.audit['models']['identity_check']=={'convert':'yes','audit':'yes'},'identity probes not recorded')
    for kind in ('audit','convert'):
        for call in c.agy_calls(kind):
            check(call['add_dir_files']=='1' and call['add_dir_pdf']=='1',kind+' workspace must contain only its chunk PDF')
    detail=c.out.with_suffix('.audit');check(detail.is_dir(),'requested audit detail was not retained')
    check(not any(p.suffix.lower()=='.pdf' or p.name.startswith(('audit-ws-','hybrid-ws-')) for p in detail.rglob('*')),'published audit detail leaked retained PDF workspaces')

def b():
    c=Case('b',rows(('audit','11-20',1,'fail:missing equation (12)'))).run();check_final_verified(c)
    ch=c.audit['chunks'][1]
    check(ch['final_source']=='pro-repair' and ch['repair']['attempted'],'repair provenance missing')
    check(ch['audit']['attempts']==2,'audit attempts must include initial and repaired versions')
    check(len(c.rows('repair','11-20'))==1,'repair count')
    check('missing equation (12)' in c.prompt('repair','11-20'),'finding absent from repair prompt')
    check(c.out.read_text().count('REPAIRED-11-20')==1,'repaired content not selected')
    bases=re.findall(r'validate basis=(\d+)B \(layout\) kind=(convert|repair) pages=11-20(?:\s|$)',c.stderr)
    native=[int(n) for n,kind in bases if kind=='convert'];repair=[int(n) for n,kind in bases if kind=='repair']
    check(native and repair and all(n==native[0] for n in native+repair),'repair and native layout validation bases differ or were not logged')

def damaged(name,behaviour,det_key=None,hint=None):
    c=Case(name,rows(('convert','11-20',1,behaviour))).run();check_final_verified(c)
    ch=c.audit['chunks'][1]
    check(ch['attempts'][0]['score']['pass'] is False,'deterministic false-PASS was accepted')
    check(ch['final_source']=='pro-repair' and ch['repair']['attempted'],'deterministic failure did not trigger repair')
    if det_key:
        first=ch['attempts'][0]
        check(det_key in json.dumps(first),'missing deterministic field '+det_key)
    if hint:check(hint in c.prompt('repair','11-20'),'deterministic hint absent: '+hint)
    if name=='b3':
        check(re.search(r'PDF sentence absent: ["“].{12}',c.prompt('repair','11-20')) is not None,'missing sentence head not quoted')
        check('missing_sentences_page_max' in ch['attempts'][0]['score']['chunk_gate']['failed'],'missing-sentence page floor must actually fail')
    if name=='b4':
        check(ch['attempts'][0]['score']['chunk_gate']['values']['injected_dois']==1,'invented DOI raw count must equal one')
        check('10.12345/fabricated-reference' not in c.out.read_text(),'invented DOI survived repair')

def b2():damaged('b2','ok_drop_numbers')
def b3():damaged('b3','ok_drop_paragraph','missing_sentences_page_max','MISSING_TEXT (deterministic)')
def b4():damaged('b4','ok_inject_doi','injected_dois','DOI not found in the PDF text layer')

def b5():
    damage=rows(('convert','11-20',1,'truncate_keep_sentinel'),('audit','11-20',1,'fail:missing truncated prose'))
    c=Case('b5',damage,{'GEMINI_PDF_KEEP_AUDIT_DIR':1,'GEMINI_PDF_MATHPIX_PAGE_CAP':0}).run();check_final_verified(c)
    ch=c.audit['chunks'][1]
    check(ch['final_source']=='pro-reconvert' and ch['repair']['attempted'],'fresh conversion provenance missing')
    check([item['kind'] for item in ch['attempts']]==['pro','pro-reconvert'],'unexpected remedy history')
    check(ch['attempts'][0]['truncation_signature'] is True and ch['attempts'][1]['truncation_signature'] is False,'first-attempt signature missing or repeated')
    check(ch['attempts'][0]['score']['completeness_ratio']<0.6,'fixture did not exercise gross truncation')
    check([(r[3],r[5]) for r in c.rows(ran='11-20')]==[('convert','1'),('audit','1'),('convert','2'),('audit','2')],'second conversion must follow a validated/audited first conversion')
    first,second=c.prompt('convert','11-20',1),c.prompt('convert','11-20',2)
    sentinel=re.search(r'<!-- END OF CHUNK ([^>]+) -->',second)
    check(sentinel is not None and sentinel[1].endswith('-11_20-c2'),'fresh c2 sentinel missing')
    check(re.sub(r'<!-- END OF CHUNK [^>]+ -->','SENTINEL',first)==re.sub(r'<!-- END OF CHUNK [^>]+ -->','SENTINEL',second),'fresh conversion changed original prompt/context')
    check('truncation_signature: true' in (c.out.with_suffix('.audit')/'chunk_11_20/hints-0.txt').read_text(),'signature hint missing')
    check(not c.mathpix() and not c.rows('repair'),'unexpected repair or Mathpix for one-round recovery')
    # Transport failures still consume one round; repeated truncation now
    # takes the independently audited bisection path exercised by scenario t.
    variants=[('b5-exhausted','exit1',1,'partial'),
              ('b5-dispatch','exit1',2,'verified')]
    for name,behaviour,rounds_max,status in variants:
        scenario=damage+rows(('convert','11-20',2,behaviour))
        x=Case(name,scenario,{'GEMINI_PDF_REPAIR_ROUNDS':rounds_max,'GEMINI_PDF_MATHPIX_PAGE_CAP':0}).run().expect(status).common()
        item=x.audit['chunks'][1]
        check(len(x.rows('convert','11-20'))==2,'fresh conversion must run once')
        check(len(x.rows('repair','11-20'))==rounds_max-1,'fresh conversion did not consume exactly one repair round')
        if status=='verified':check(item['final_source']=='pro-repair','remaining repair round was not selected')
        else:check(item['status']=='unverified','exhausted fresh attempt accepted')

def c():
    x=Case('c',FAIL_LAST).run();check_final_verified(x)
    ch=x.audit['chunks'][2]
    check(ch['final_source']=='mathpix' and ch['mathpix']['pages']==5,'Mathpix chunk provenance')
    check(x.audit['mathpix']['pages_used']==5,'Mathpix pages used')
    check(len(x.mathpix())==1 and x.mathpix()[0][1]=='5','must upload 5-page chunk, never 25')
    check(len(x.rows('audit','21-25'))==3,'Mathpix cleanup was not audited')
    check(x.out.read_text().count('MATHPIX-')==1,'Mathpix content missing or duplicated')
    hybrid=x.agy_calls('hybrid','21-25');check(len(hybrid)==1,'expected one hybrid cleanup call')
    check(hybrid[0]['has_add_dir']=='1' and hybrid[0]['add_dir_pdf']=='1','hybrid cleanup cannot see chunk PDF')
    check(int(hybrid[0]['stdin_bytes'])>0 and hybrid[0]['stdin_marker']=='1','hybrid stdin did not carry Mathpix Markdown')

def d():
    x=Case('d',FAIL_LAST,{'GEMINI_PDF_MATHPIX_PAGE_CAP':4}).run().expect('partial').common()
    ch=x.audit['chunks'][2]
    check(ch['status']=='unverified' and not ch['mathpix']['attempted'] and ch['mathpix']['reason']=='mathpix-cap','cap failure classification')
    check(not x.mathpix() and x.audit['mathpix']['pages_used']==0,'cap allowed dispatch')
    check('<!-- AUDIT FAILED (pages 21–25, chunk 3, mathpix-cap) -->' in x.out.read_text(),'unverified marker absent')
    check((x.root/'ref/md/out.md').exists(),'partial must sync under D11')
    check(any(r['outcome']=='skipped:cap' for r in x.ledger()),'cap decision missing from ledger')
    strict=Case('d-strict',FAIL_LAST,{'GEMINI_PDF_MATHPIX_PAGE_CAP':4,'GEMINI_PDF_STRICT':1}).run().expect('partial',4).common()

def d2():
    extra=rows(('audit','11-20',1,'fail:x'),('audit','11-20',2,'fail:x'))
    x=Case('d2',extra+FAIL_LAST,{'GEMINI_PDF_MATHPIX_PAGE_CAP':12}).run().expect('partial').common()
    check(x.audit['mathpix']['pages_used']==10,'partial cap reservation')
    check(x.audit['chunks'][1]['final_source']=='mathpix','first reservation not accepted')
    check(x.audit['chunks'][2]['mathpix']['reason']=='mathpix-cap','later cap failure missing')

def e():
    x=Case('e',GARBAGE).run().expect('degraded').common()
    ch=x.audit['chunks'][1]
    check(ch['status']=='unaudited' and ch['audit']['status']=='unparseable' and ch['audit']['attempts']==2 and ch['score']['pass'],'invalid audits must never PASS')
    check('<!-- AUDIT: unaudited (pages 11–20,' in x.out.read_text(),'evidence gap marker absent')
    check((x.root/'ref/md/out.md').exists(),'degraded output did not sync')

def f():
    x=Case('f',rows(('audit','11-20',1,'sleep:6')),{'GEMINI_PDF_DEADLINE':30,'GEMINI_PDF_AUDIT_MIN':14,'GEMINI_PDF_REPAIR_MIN':18,'GEMINI_PDF_MATHPIX_MIN':22}).run().expect('degraded').common()
    check(x.audit['deadline']['hit'],'deadline skip not recorded')
    check(x.audit['totals']['failed']==0,'optional audit deadline lost mandatory content')
    check(x.audit['chunks'][2]['audit']['status']=='skipped' and x.audit['chunks'][2]['audit']['reason']=='deadline','last audit was not skipped')
    check(not x.rows('audit','21-25'),'last audit dispatched after floor')
    check(x.audit['skipped_steps'] and all(s['reason']=='deadline' for s in x.audit['skipped_steps']),'wrong skip classification')

def f2():
    x=Case('f2',FAIL_LAST,{'GEMINI_PDF_DEADLINE':60,'GEMINI_PDF_MATHPIX_MIN':100}).run().expect('partial').common()
    ch=x.audit['chunks'][2]
    check(ch['status']=='unverified' and ch['mathpix']['reason']=='deadline','Mathpix deadline classification')
    check(ch['repair']['attempted'],'repair should run while higher Mathpix floor skips')
    check(not x.mathpix(),'Mathpix dispatched under its floor')
    check('deadline) -->' in x.out.read_text(),'deadline residual marker absent')

def f3():
    x=Case('f3',rows(('convert','1-10',1,'sleep:3')),{'GEMINI_PDF_DEADLINE':14,'GEMINI_PDF_CONVERT_MIN':5,'GEMINI_PDF_AUDIT_MIN':20,'GEMINI_PDF_REPAIR_MIN':20,'GEMINI_PDF_MATHPIX_MIN':20}).run().expect('failed',2).common()
    check(not x.out.exists() and x.out.with_suffix('.failed.md').exists(),'failed output must not be ingestible')
    check(x.audit['totals']['failed']==2,'remaining chunks must fail without dispatch')
    check(len(x.rows('convert'))==1,'deadline allowed later mandatory call')
    check(all(ch['audit']['reason']=='deadline' for ch in x.audit['chunks'][1:]),'deadline failure reason')
    check(x.elapsed<=21,'deadline + convert_min + grace overrun')

    early=Case('f3-preflight',env={'GEMINI_PDF_DEADLINE':0}).run().expect('failed',2).common(notices=0)
    check(not early.out.exists() and early.out.with_suffix('.failed.md').exists(),'expired preflight left ingestible output')
    check(not early.rows() and not early.mathpix(),'expired preflight dispatched external content')
    check(early.audit['deadline']['hit'],'expired preflight deadline not recorded')


def final_artifacts(x,previous=None):
    x.common()
    failed=x.out.with_suffix('.failed.md')
    check(x.out.exists()!=failed.exists(),'exactly one OUTPUT.md or OUTPUT.failed.md must exist')
    check(x.out.with_suffix('.audit.json').exists() and x.audit,'missing or empty audit report')
    for key in ('schema','status','summary_line','run_nonce'):check(key in x.audit,'minimal report missing '+key)
    check(x.audit['schema']==1,'minimal report schema must be 1')
    summaries=[line for line in x.stderr.splitlines() if line.startswith('[gemini-pdf] AUDIT:')]
    check(len(summaries)==1,'finalization must print exactly one AUDIT summary')
    check(summaries[0]==x.audit['summary_line'] or summaries[0]=='[gemini-pdf] '+x.audit['summary_line'],'minimal report summary differs from stderr')
    status=x.audit['status'];strict=x.env.get('GEMINI_PDF_STRICT')=='1'
    mapped={'verified':0,'disabled':0,'degraded':3 if strict else 0,'partial':4 if strict else 0,'failed':2}
    check(status in mapped and x.rc==mapped[status],f'reported {status} does not match exit {x.rc}')
    check(f'status={status} ' in summaries[0],'AUDIT status differs from report')
    check(x.stdout==str(x.out if x.out.exists() else failed)+'\n','final stdout must name the routed Markdown file')
    if previous is not None:
        preserved=list(x.root.glob('out.md.pre-*'))
        check(len(preserved)==1,'previous output must remain on every outcome')
        expected=previous if isinstance(previous,bytes) else previous.encode()
        check(preserved[0].read_bytes()==expected,'preserved previous output changed')
        notice='WARN: previous output preserved at '+str(preserved[0])
        check(x.stderr.count(notice)==2,'previous output must be named at move-aside and in the finalize summary')


def real_chunk_states(x,expected):
    check(x.audit['totals']['chunks']==3,'report must retain all three real fixture chunks')
    chunks=x.audit['chunks']
    check([(c['id'],c['first_page'],c['last_page']) for c in chunks]==[(1,1,10),(2,11,20),(3,21,25)],'report invented, omitted, or reordered a real chunk')
    check([c['status'] for c in chunks]==expected,'reported per-chunk statuses differ from the run')
    detail=x.out.with_suffix('.audit')
    states=sorted((read_json(p) for p in detail.glob('chunk_*/state.json')),key=lambda s:s['id'])
    check(len(states)==3,'retained evidence must contain all three real chunk states')
    check([(s['id'],s['first_page'],s['last_page'],s['status']) for s in states]==[(c['id'],c['first_page'],c['last_page'],c['status']) for c in chunks],'report differs from real on-disk chunk states')
    for status in ('verified','unaudited','unverified','failed'):
        check(x.audit['totals'][status]==expected.count(status),'report total differs from real '+status+' states')


def f4():
    # Three known two-second conversions plus the one-second publish reserve
    # exhaust the seven-second budget even before optional/local work.
    x=Case('f4',rows(('convert','*','*','sleep:2')),{'GEMINI_PDF_DEADLINE':7})
    previous='previous trustworthy deadline content\n';x.out.write_text(previous);x.run()
    final_artifacts(x,previous)
    check(x.audit['status']=='failed' or x.audit.get('deadline',{}).get('hit'),'tight deadline was not reflected in the audit')
    # The final mandatory call outlives its remaining budget. The portable
    # watchdog's two-second kill grace spends the publish reserve too.
    expired=Case('f4-expired',rows(('convert','1-10',1,'sleep:10')),{'GEMINI_PDF_DEADLINE':8,'GEMINI_PDF_WATCHDOG_GRACE':0})
    expired.out.write_text(previous);expired.run();final_artifacts(expired,previous)
    check(expired.rc==2 and expired.audit['status']=='failed','exhausted conversion must report failed/2')
    real_chunk_states(expired,['failed']*3)
    check('report-error' not in expired.audit['degradations'],'fixed finalize allowance must permit the normal assembler after an overrun')
    check(len(expired.rows('convert'))==1 and expired.rows('convert')[0][6]=='sleep:10','exhausted conversion did not dispatch exactly once')
    check('agy timed out after' in expired.stderr,'final conversion must reach the portable watchdog')
    check(expired.elapsed>=8 and expired.audit['deadline']['elapsed_s']>=8,'final conversion did not consume the entire deadline')


def f5():
    x=Case('f5',env={'FAKE_JQ_FAIL':'report'}).run().expect('degraded');final_artifacts(x)
    real_chunk_states(x,['verified']*3)
    check(all(len(c['attempts'])==1 and c['attempts'][0]['selected'] and c['score']['pass'] for c in x.audit['chunks']),'emergency assembler lost real attempt/score evidence')
    check(len(x.rows('convert'))==len(x.rows('audit'))==3,'report-only jq death altered conversion or audit calls')
    check((x.root/'fake-jq-report-failure').exists(),'report-phase jq fault was not reached')
    check('report-error' in x.audit.get('degradations',[]),'jq report failure did not record report-error degradation')
    # Fault injection is confined to this case's script copy, after all chunk
    # states exist and before ordinary finalization can assemble their report.
    y=Case('f5-exit')
    previous='previous trustworthy emergency content\n';y.out.write_text(previous)
    stale='obsolete failed output from an earlier run\n';y.out.with_suffix('.failed.md').write_text(stale)
    broken=y.root/'scripts';shutil.copytree(RUN/'scripts',broken)
    target=broken/'pdf_to_markdown.sh';source=target.read_text()
    seam='\n_finalize "${ATTEMPT_MDS[$WINNER_IDX]}"'
    check(source.count(seam)==1,'EXIT fault injection seam must be unique')
    target.write_text(source.replace(seam,'\necho "offline fixture: EXIT after chunk states" >&2\nexit 97\n'+seam,1))
    y.run(script=target).expect('degraded');final_artifacts(y,previous)
    real_chunk_states(y,['verified']*3)
    check(all(len(c['attempts'])==1 and c['attempts'][0]['selected'] for c in y.audit['chunks']),'EXIT recovery lost the selected real attempts')
    check('report-error' in y.audit['degradations'],'EXIT recovery must record report-error')
    check('offline fixture: EXIT after chunk states' in y.stderr,'post-state EXIT fault was not reached')
    check(stale not in y.out.read_text(),'emergency publication retained stale failed content')
    # An abort before any chunk exists must have zero counts, not a fabricated
    # failed chunk. Keep this separate from recovery of actual chunk evidence.
    early=Case('f5-aborted');early.out.write_text(previous)
    broken=early.root/'scripts';shutil.copytree(RUN/'scripts',broken);(broken/'audit_tools.py').unlink()
    early.run(script=broken/'pdf_to_markdown.sh').expect('failed',2);final_artifacts(early,previous)
    check(early.audit.get('aborted') is True and early.audit['chunks']==[],'pre-chunk abort must identify its absent chunk evidence')
    check(all(early.audit['totals'][key]==0 for key in ('chunks','verified','unaudited','unverified','failed')),'pre-chunk abort fabricated content status counts')
    check('Audit helper not found:' in early.stderr,'emergency setup fault was not reached')


def g():
    x=Case('g').run('--no-audit').common()
    check(x.rc==0 and x.out.exists(),'legacy conversion failed')
    check(not x.rows('audit') and not x.audit,'legacy dispatched audits')
    x=Case('g-sanitize')
    copied=x.root/'scripts';shutil.copytree(RUN/'scripts',copied)
    marker='<!-- OFFLINE LEGACY SANITIZER CLEANUP -->'
    (copied/'sanitize_md.py').write_text('import pathlib, sys\npath=pathlib.Path(sys.argv[1])\npath.write_text(path.read_text()+"\\n'+marker+'\\n")\n')
    x.run('--no-audit',script=copied/'pdf_to_markdown.sh').common()
    check(x.rc==0 and marker in x.out.read_text(),'legacy cleanup did not reach published Markdown')
    check(not x.rows('audit') and not x.audit,'legacy sanitizer enabled audit')

def g2():
    scenario=rows(('convert','*','*','ok_drop_half'),('hybrid','*','*','ok_drop_half'))
    x=Case('g2-open',scenario,{'GEMINI_PDF_MATHPIX_PAGE_CAP':50}).run('--no-audit').common()
    check(x.rc==0 and len(x.mathpix())==2,'legacy chain must reach both Mathpix stages')
    base=Case('g2-cap',scenario,{'GEMINI_PDF_MATHPIX_PAGE_CAP':20}).run('--no-audit').common()
    check(base.rc==0 and base.out.exists(),'legacy capped path failed to retain best effort')
    check(not base.mathpix(),'over-cap legacy path called helper')
    check(sum(r['outcome']=='skipped:cap' for r in base.ledger())==2,'both legacy stages must ledger cap skips')
    check(base.stderr.count('PAID: Mathpix SKIPPED')==2,'legacy skips not loud')


def g3():
    scenario=rows(('convert','*','*','sleep:1'))
    x=Case('g3-unset',scenario).run('--no-audit').common()
    check(x.rc==0 and len(x.rows('convert'))==3,'unset legacy deadline must not gate conversion')
    y=Case('g3-set',rows(('convert','1-10',1,'sleep:3')),{'GEMINI_PDF_DEADLINE':7}).run('--no-audit').common()
    check(len(y.rows('convert'))<3,'explicit legacy deadline must gate mandatory calls')
    check(y.elapsed<=11,'legacy explicit deadline bound')

def h():Case('h',GARBAGE,{'GEMINI_PDF_STRICT':1}).run().expect('degraded',3).common()

def k2():
    scenario=rows(('convert','1-10',1,'sleep:1'))
    x=Case('k2-first',scenario);y=Case('k2-second',scenario)
    y.env['GEMINI_PDF_LOCK_DIR']=x.env['GEMINI_PDF_LOCK_DIR'];y.env['FAKE_AGY_OVERLAP_DIR']=x.env['FAKE_AGY_OVERLAP_DIR']
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        first=pool.submit(x.run);time.sleep(.2);second=pool.submit(y.run);first.result();second.result()
    check_final_verified(x);check_final_verified(y)
    check('Waiting for agy lock' in y.stderr,'second converter did not wait on shared lock')
    check(all(r[8]=='0' for r in x.rows()+y.rows()),'shared lock allowed overlap')

def l():
    x=Case('l',rows(('convert','11-20',1,'hang'))).run();check_final_verified(x)
    check(len(x.rows('convert','11-20'))==2,'watchdog did not retry')
    check('agy timed out after 8s (watchdog)' in x.stderr,'watchdog log missing')
    pids=[int(row[1]) for row in x.rows()]
    children=x.root/'agy.tsv.children'
    check(children.exists(),'hang test did not record worker child')
    pids.extend(int(pid) for pid in children.read_text().splitlines())
    for pid in pids:
        try:os.kill(pid,0)
        except ProcessLookupError:continue
        raise AssertionError('fake agy process survived watchdog')
    check(x.elapsed<40,'watchdog scenario exceeded 40 s')  # Allow watchdog, retry, and audit work under the six-worker pool.

def m():
    x=Case('m',rows(('convert','1-10',1,'truncate'))).run();check_final_verified(x)
    check(len(x.rows('convert','1-10'))==2,'truncation did not retry')
    check(x.out.read_text().count('title: Synthetic Econometric Pipeline Paper')==1,'front matter duplicated')

def n():
    x=Case('n',FAIL_LAST,{'FAKE_MATHPIX_BEHAVIOUR':'fail'}).run().expect('partial').common()
    ch=x.audit['chunks'][2]
    check(ch['status']=='unverified' and ch['mathpix']['attempted'] and ch['mathpix']['reason']=='mathpix-error','Mathpix error classification')
    check(any(r['outcome']=='failed' for r in x.ledger()),'failed outcome absent')

def n2():
    x=Case('n2',rows(('convert','1-10',1,'neterr'),('convert','1-10',2,'ok'))).run();check_final_verified(x)
    calls=x.agy_calls('convert','1-10')
    check(len(calls)==2 and [call['behaviour'] for call in calls]==['neterr','ok'],'transient error did not retry exactly once')
    check(len({(call['kind'],call['range'],call['attempt'],call['prompt_hash'],call['args_hash']) for call in calls})==1,'transient retry changed the caller attempt or arguments')
    check([call['dispatch'] for call in calls]==['1','2'] and [call['transient_retry'] for call in calls]==['0','1'],'fake ledger lost dispatch/transport attempt distinction')
    check(int(calls[1]['timestamp'])-int(calls[0]['timestamp'])>=20000,'transient retry omitted the 20-second pause')
    check(x.stderr.count('WARN: transient agy error — retrying once')==1,'transient retry warning missing or duplicated')
    check(x.audit['totals']['convert_calls']==len(x.rows('convert'))==4,'conversion totals omitted transient dispatch')
    check(len(x.audit['chunks'][0]['attempts'])==1,'transport retry consumed a repair attempt')
    check(not x.rows('repair') and not x.mathpix(),'successful transient conversion entered remediation')
    transient_retry_unit_checks(x.root)

def transient_retry_unit_checks(root):
    source=SCRIPT.read_text()
    helper=source[source.index('_run_agy() {'):source.index('\n_run_agy_model() {')]
    # Exercise retry policy without spending additional real backoffs. The
    # dispatch stub records calls, stderr classification, and available budget.
    harness=r'''set -eu
calls=0
budget_left="$5"
_log() { printf '%s\n' "$*" >&2; }
_deadline_enabled() { test "$budget_left" != off; }
_remaining() { printf '%s\n' "$budget_left"; }
sleep() { printf '%s\n' "$1" >> sleep.log; test "$budget_left" = off || budget_left=$((budget_left - $1)); }
_run_agy_once() {
    calls=$((calls + 1))
    if test "$calls" -eq 1; then
        printf '%s\n' "$first_error" > "$5"
        printf '%s\n' "$first_output" > "$4"
        CALL_REASON="agy-exit:$first_rc"
        test "$first_rc" -ne 124 || CALL_REASON=timeout
        test "$first_rc" -ne 0 || CALL_REASON=''
        return "$first_rc"
    fi
    if test "$budget_left" != off && test "$budget_left" -lt 4; then CALL_REASON=deadline; return 126; fi
    CALL_REASON="agy-exit:$second_rc"
    test "$second_rc" -ne 0 || CALL_REASON=''
    return "$second_rc"
}
first_error="$1"; first_output="$2"; first_rc="$3"; second_rc="$4"
'''+helper+r'''
if _run_agy dir . prompt out.raw err.log; then rc=0; else rc=$?; fi
printf '%s %s %s\n' "$rc" "$calls" "$CALL_REASON"
'''
    cases=[
        ('second-failure','network issue','',1,1,'off','1 2 agy-exit:1','20'),
        ('deadline-clamp','no such host','',1,0,'7','126 2 deadline','7'),
        ('expired','connection reset','',1,0,'0','126 2 deadline',''),
        ('tool-denied','network issue; auto-denied','',1,0,'off','1 1 agy-exit:1',''),
        ('model-error','network issue','invalid model selection',1,0,'off','1 1 agy-exit:1',''),
        ('watchdog','timeout awaiting response','',124,0,'off','124 1 timeout',''),
        ('ordinary-error','some other error','',1,0,'off','1 1 agy-exit:1',''),
        ('successful-call','network issue','',0,0,'off','0 1',''),
    ]
    cases.extend(('positive-'+str(i),error,'',1,0,'off','0 2','20') for i,error in enumerate(('Eligibility check failed','network issue','no such host','connection reset','timeout awaiting response')))
    for name,error,output,first,second,budget,expected,nap in cases:
        directory=root/name;directory.mkdir()
        result=subprocess.run(['bash','-c',harness,'retry-unit',error,output,str(first),str(second),budget],cwd=directory,text=True,capture_output=True)
        check(result.returncode==0 and result.stdout.strip()==expected,'transient retry '+name+': '+result.stdout+result.stderr)
        pauses=(directory/'sleep.log').read_text().strip() if (directory/'sleep.log').exists() else ''
        check(pauses==nap,'transient retry '+name+' incorrect deadline-clamped backoff: '+pauses)
        if nap:check((directory/'err_transient.log').read_text().strip()==error,'transient retry lost original stderr')

def o():
    x=Case('o').run('--bib-key','synthkey');check_final_verified(x)
    check((x.root/'ref/md/synthkey.md').exists(),'bib-key sync missing')
    for suffix in ('.err','.meta.json','.quality.json','.audit.json'):check(x.out.with_suffix(suffix).exists(),'missing interface sidecar '+suffix)
    check(all(len(ch)==7 for ch in read_json(x.out.with_suffix('.meta.json'))['chunks']),'.meta.json chunk shape changed')
    subprocess.run(['bash','-n',str(SCRIPT)],check=True)
    for file in [SOURCE/name for name in ('pdf_to_markdown.sh','mathpix_extract.py','quality_check.py')]:
        if file.suffix in ('.sh','.py'):
            for line in file.read_text().splitlines():
                check('/'+ 'Users/' not in line,'absolute user path')

def p():
    x=Case('p',env={'GEMINI_PDF_MODEL':''}).run().common(notices=0)
    check(x.rc==1,'empty model must fail with usage/environment exit 1')
    check('GEMINI_PDF_MODEL' in x.stderr and ('empty' in x.stderr.lower() or 'non-empty' in x.stderr.lower()),'empty pin failure must be loud')
    check(not x.rows(),'empty model dispatched content')

def r():
    x=Case('r')
    x.env.pop('REFERENCE_DIR',None)
    x.env.pop('GEMINI_PDF_REFERENCE_DIR',None)
    x.run();check_final_verified(x)
    check(not (x.root/'ref/md/out.md').exists(),'converter synced without explicit destination')


def q():
    previous='previous trustworthy content\n'
    x=Case('q',rows(('convert','*','*','exit1')));x.out.write_text(previous);x.run('--no-fallback').expect('failed',2).common();final_artifacts(x,previous)
    check(not x.out.exists(),'failed conversion retained ingestible OUTPUT.md')
    check(x.out.with_suffix('.failed.md').exists(),'failed best effort file absent')
    preserved=list(x.root.glob('out.md.pre-*'));check(len(preserved)==1 and preserved[0].read_text()=='previous trustworthy content\n','old output was not preserved')
    check('WARN: previous output preserved at' in x.stderr,'move-aside path not announced')
    check('<!-- CHUNK ' in x.out.with_suffix('.failed.md').read_text(),'failed chunk placeholder absent')
    check(not (x.root/'ref/md/out.md').exists(),'failed output synced')
    check(any(r[4]=='1-5' for r in x.rows('convert')),'existing bisect path not exercised')


def s1():
    # Capture the audited input before the copied sanitizer fails so the
    # publication assertion compares bytes, independent of sanitizer behavior.
    stub='import pathlib, sys\npath=pathlib.Path(sys.argv[1])\npathlib.Path(__file__).with_name("sanitize-input.md").write_bytes(path.read_bytes())\npath.write_text("CORRUPTED BY FAILED OFFLINE SANITIZER\\n")\nraise SystemExit(1)\n'
    variants=[('s1','',{},'degraded',0),
              ('s1-strict','',{'GEMINI_PDF_STRICT':1},'degraded',3),
              ('s1-partial',FAIL_LAST,{'GEMINI_PDF_MATHPIX_PAGE_CAP':4},'partial',0),
              ('s1-failed','',{'GEMINI_PDF_DEADLINE':0},'failed',2)]
    for name,scenario,env,status,rc in variants:
        x=Case(name,scenario,env)
        copied=x.root/'scripts';shutil.copytree(RUN/'scripts',copied)
        (copied/'sanitize_md.py').write_text(stub)
        x.run(script=copied/'pdf_to_markdown.sh').expect(status,rc);final_artifacts(x)
        check('sanitize-failed' in x.audit['degradations'],'sanitize failure must remain a finalization degradation')
        check(any(s['step']=='sanitize' and s['reason']=='local-step' for s in x.audit['skipped_steps']),'sanitize failure must record the skipped finalize step')
        check('WARN: sync skipped: sanitize failed' in x.stderr,'sanitize failure must announce skipped vault sync')
        check(not (x.root/'ref/md/out.md').exists(),'unsanitized Markdown must not enter the vault')
        actual=x.out if status!='failed' else x.out.with_suffix('.failed.md')
        check(actual.read_bytes()==(copied/'sanitize-input.md').read_bytes(),'sanitize failure altered the audited Markdown')
        if status=='failed':
            check(x.audit.get('aborted') is True and x.audit['chunks']==[],'preflight sanitize failure must preserve the zero-chunk abort')
            check(all(x.audit['totals'][key]==0 for key in ('chunks','verified','unaudited','unverified','failed')),'sanitize failure fabricated chunk counts after preflight abort')
            check(not list(x.out.with_suffix('.audit').glob('chunk_*/state.json')),'sanitize failure created a synthetic state after preflight abort')
        elif status=='degraded':real_chunk_states(x,['verified']*3)
        else:real_chunk_states(x,['verified','verified','unverified'])


def t():
    truncated=rows(('convert','11-20',1,'truncate_keep_sentinel'),
                   ('audit','11-20',1,'fail:page-missing:20'),
                   ('convert','11-20',2,'truncate_keep_sentinel'),
                   ('audit','11-20',2,'fail:page-missing:20'),
                   ('convert','11-15',1,'ok'),('convert','16-20',1,'ok'))
    x=Case('t',truncated,{'GEMINI_PDF_KEEP_AUDIT_DIR':1,'GEMINI_PDF_MATHPIX_PAGE_CAP':0}).run().expect('verified').common()
    check(x.audit['totals']['chunks']==x.audit['totals']['verified']==4,'bisection must replace one chunk with two verified halves')
    chunks=x.audit['chunks']
    expected=[(1,1,10),('2.1',11,15),('2.2',16,20),(3,21,25)]
    check([(ch['id'],ch['first_page'],ch['last_page']) for ch in chunks]==expected,'bisected IDs/ranges or later parent ID changed')
    check(all(ch.get('bisected') is True and ch['final_source']=='pro' for ch in chunks[1:3]),'half provenance or bisection marker missing')
    check(len(x.rows('convert','11-20'))==2 and not x.rows('repair') and not x.mathpix(),'repeated truncation did not go directly to halves')
    check(len(x.rows('audit','11-20'))==2,'fresh conversion must be scored and audited before bisection')
    detail=x.out.with_suffix('.audit')
    half_ranges=('11-15','16-20')
    for ran in half_ranges:
        check(len(x.rows('convert',ran))==len(x.rows('audit',ran))==1,'half was not independently converted and audited: '+ran)
    sentinels=[re.search(r'<!-- END OF CHUNK ([^>]+) -->',x.prompt('convert',ran))[1] for ran in half_ranges]
    parent_sentinels=[re.search(r'<!-- END OF CHUNK ([^>]+) -->',x.prompt('convert','11-20',attempt))[1] for attempt in (1,2)]
    check(len(set(sentinels+parent_sentinels))==4,'halves reused a parent or sibling sentinel')
    first_final=(detail/'chunk_11_15/final.md').read_bytes()
    accepted_tail=first_final[-1500:].decode().rstrip('\n')
    second_prompt=x.prompt('convert','16-20')
    check(accepted_tail in second_prompt and 'BEGIN PRIOR-OUTPUT CONTEXT' in second_prompt,'second half did not receive the accepted first-half tail')
    check('chunks=4 verified=4 ' in x.stderr.splitlines()[-1],'AUDIT summary retained the parent chunk count')
    published=x.out.read_text()
    for page in range(11,21):
        check(published.count(str(3000+page))==1,'parent output was retained or a half page was omitted: '+str(page))

    invalid=Case('t-invalid',rows(('convert','11-20',1,'truncate_keep_sentinel'),
                 ('audit','11-20',1,'fail:page-missing:20'),('convert','11-20',2,'nosentinel')),
                 {'GEMINI_PDF_MATHPIX_PAGE_CAP':0}).run().expect('verified').common()
    check(invalid.audit['totals']['chunks']==4 and [ch['id'] for ch in invalid.audit['chunks']][1:3]==['2.1','2.2'],'invalid fresh output did not bisect')
    check(len(invalid.rows('audit','11-20'))==1 and not invalid.rows('repair'),'invalid fresh output was audited or sent to edit repair')

    # Repeating the same damage in a half must stop at depth one and publish
    # that half with the ordinary unverified marker, never quarter-chunks.
    half_damage=truncated+rows(('convert','11-15',2,'truncate_keep_sentinel'),
                              ('audit','11-15','*','fail:page-missing:15'))
    half_damage=half_damage.replace('convert\t11-15\t1\tok\n','convert\t11-15\t1\ttruncate_keep_sentinel\n')
    depth=Case('t-depth',half_damage,{'GEMINI_PDF_MATHPIX_PAGE_CAP':0}).run().expect('partial').common()
    check(depth.audit['totals']['chunks']==4 and depth.audit['totals']['unverified']==1,'failed half changed the final chunk partition')
    half=depth.audit['chunks'][1]
    check(half['id']=='2.1' and half['status']=='unverified' and half.get('bisected') is True,'failed half lost status or provenance')
    check(set(row[4] for row in depth.rows('convert'))=={'1-10','11-20','11-15','16-20','21-25'},'bisection exceeded depth one')
    check('<!-- AUDIT FAILED (pages 11–15, chunk 2.1,' in depth.out.read_text(),'failed half lacks the ordinary residual marker')

    deadline=Case('t-deadline',truncated,{'GEMINI_PDF_DEADLINE':60,'GEMINI_PDF_REPAIR_MIN':100,
                  'GEMINI_PDF_MATHPIX_PAGE_CAP':0}).run('--no-fallback').expect('partial').common()
    check(len(deadline.rows('convert','11-20'))==2,'repair floor incorrectly prevented the fresh conversion')
    check(deadline.audit['totals']['chunks']==3 and not deadline.rows('convert','11-15') and not deadline.rows('convert','16-20'),'bisection dispatched below its optional-work floor')
    check(deadline.audit['chunks'][1]['reason']=='deadline','skipped bisection did not retain reason deadline')
    check(any(step['step']=='bisect' and step['reason']=='deadline' for step in deadline.audit['skipped_steps']),'bisection deadline skip was not recorded')


def u():
    x=Case('u',env={'FAKE_AGY_MODEL_PROBE_FAIL':'flash'}).run().expect('degraded').common()
    check(not x.rows('audit'),'unverified audit model dispatched')
    check(x.audit['models']['identity_check']['audit'] in ('no','unknown'),'failed audit identity not recorded')
    check(all(ch['status']=='unaudited' and ch['audit']['reason']=='audit-model-unavailable' for ch in x.audit['chunks']),'audit unavailable classification')

def v():
    x=Case('v-twice',rows(('audit','11-20',1,'deny'),('audit','11-20',2,'deny'))).run().expect('degraded').common()
    ch=x.audit['chunks'][1]
    check(ch['status']=='unaudited' and ch['audit']['reason']=='tool-denied' and ch['audit']['attempts']==2,'tool denial treated as pass')
    check('jetski: no output produced' in x.out.with_suffix('.err').read_text(),'audit denial stderr not collected')
    y=Case('v-once',rows(('audit','11-20',1,'deny')),{'GEMINI_PDF_AUDIT_JSON_MODE':'schema'}).run();check_final_verified(y)
    check(y.audit['chunks'][1]['audit']['attempts']==2,'schema denial did not retry')


    repair_rows=rows(('audit','1-25',1,'fail:repair transport'),('repair','1-25',1,'deny'))
    repaired=Case('v-repair',repair_rows).run('--no-chunk').expect('verified').common()
    check(len(repaired.rows('repair'))==2 and repaired.audit['chunks'][0]['final_source']=='pro-repair','tool-denied repair did not retry once')
    hybrid_rows=rows(('audit','1-25',1,'fail:hybrid transport'),('audit','1-25',2,'fail:hybrid transport'),('hybrid','1-25',1,'deny'))
    hybrid=Case('v-hybrid',hybrid_rows,{'GEMINI_PDF_MATHPIX_PAGE_CAP':25}).run('--no-chunk').expect('verified').common()
    check(len(hybrid.rows('hybrid'))==2 and len(hybrid.mathpix())==1,'hybrid retry re-extracted or did not retry once')
    check(hybrid.audit['chunks'][0]['final_source']=='mathpix','hybrid recovery provenance')


def v2():
    scenario=rows(('audit','11-20',1,'fail:repair transport'),
                  ('repair','11-20',1,'deny'),('repair','11-20',2,'ok'))
    x=Case('v2',scenario).run();check_final_verified(x)
    calls=x.agy_calls('repair','11-20')
    check([call['behaviour'] for call in calls]==['deny','ok'],'denied repair did not retry exactly once')
    check(x.audit['totals']['repair_calls']==len(calls)==2,'repair call total omitted denied dispatch')
    check(x.audit['chunks'][1]['final_source']=='pro-repair','retried repair output was not selected')
    check(len({(call['prompt_hash'],call['args_hash']) for call in calls})==1,'tool-denial retry changed the call')
    check(x.stderr.count('WARN: tool call denied by headless agy — retrying once')==1,'tool-denial warning missing or duplicated')
    check(not x.mathpix(),'recovered repair unexpectedly fell back to Mathpix')

    denied=Case('v2-twice',rows(('audit','11-20',1,'fail:repair transport'),
                ('repair','11-20','*','deny')),{'GEMINI_PDF_MATHPIX_PAGE_CAP':0}).run().expect('partial').common()
    check(len(denied.rows('repair','11-20'))==denied.audit['totals']['repair_calls']==2,'second tool denial retried more than once or lost its count')
    check(denied.audit['chunks'][1]['repair']['outcome']=='tool-denied','second denial outcome changed')
    check(denied.stderr.count('WARN: tool call denied by headless agy — retrying once')==1,'second denial emitted another retry warning')

    converted=Case('v2-reconvert',rows(('convert','11-20',1,'truncate_keep_sentinel'),
                   ('audit','11-20',1,'fail:page-missing:20'),('convert','11-20',2,'deny'),
                   ('convert','11-20',3,'ok')),{'GEMINI_PDF_MATHPIX_PAGE_CAP':0}).run();check_final_verified(converted)
    calls=converted.agy_calls('convert','11-20')
    check([call['behaviour'] for call in calls]==['truncate_keep_sentinel','deny','ok'],'fresh conversion denial was not retried once')
    check(converted.audit['totals']['convert_calls']==len(converted.rows('convert'))==5,'fresh conversion retry missing from convert call total')
    check(converted.audit['chunks'][1]['final_source']=='pro-reconvert' and not converted.rows('repair'),'fresh conversion denial entered edit repair')
    check(len({(call['prompt_hash'],call['args_hash']) for call in calls[1:]})==1,'fresh conversion retry changed the call')
    check(converted.stderr.count('WARN: tool call denied by headless agy — retrying once')==1,'fresh conversion denial warning missing or duplicated')
    tool_denial_unit_checks(x.root)


def tool_denial_unit_checks(root):
    source=SCRIPT.read_text()
    helper=source[source.index('_run_checked_agy() {'):source.index('\n_reconvert_candidate() {')]
    harness=r'''set -eu
test_dispatches=0
AGY_KIND=repair
CALL_REASON=''
_log() { printf '%s\n' "$*" >&2; }
_step_allowed() { if test "$test_floor" = denied; then CALL_REASON=deadline; return 1; fi; }
_run_agy_model() {
    test_dispatches=$((test_dispatches + 1))
    : > "$5"; : > "$6"
    if test "$test_dispatches" -eq 1; then
        printf '%s' "$test_stdout" > "$5"
        printf '%s' "$test_stderr" > "$6"
        CALL_REASON="$test_reason"
        return "$test_first_rc"
    fi
    if test "$test_second_rc" -ne 0; then
        printf '%s\n' 'auto-denied' > "$6"
        CALL_REASON=agy-exit:1
        return "$test_second_rc"
    fi
    printf '%s\n' 'valid candidate' > "$5"
    CALL_REASON=''
}
test_stdout="$1"; test_stderr="$2"; test_reason="$3"
test_first_rc="$4"; test_second_rc="$5"; test_floor="$6"
'''+helper+r'''
if _run_checked_agy model dir . prompt out.raw err.log; then test_rc=0; else test_rc=$?; fi
printf '%s %s %s\n' "$test_rc" "$test_dispatches" "$CALL_REASON"
'''
    cases=[
        ('nonzero-stderr','','auto-denied','agy-exit:1',1,0,'allowed','0 2',1),
        ('nonzero-stdout','tool denied','','agy-exit:1',1,0,'allowed','0 2',1),
        ('second-denial','','auto-denied','agy-exit:1',1,1,'allowed','1 2 tool-denied',1),
        ('ordinary-failure','','ordinary failure','agy-exit:1',1,0,'allowed','1 1 agy-exit:1',0),
        ('watchdog','','auto-denied','timeout',124,0,'allowed','124 1 timeout',0),
        ('deadline','','auto-denied','agy-exit:1',1,0,'denied','1 1 deadline',0),
    ]
    for name,out,error,reason,first,second,floor,expected,warnings in cases:
        directory=root/('tool-'+name);directory.mkdir()
        result=subprocess.run(['bash','-c',harness,'denial-unit',out,error,reason,str(first),str(second),floor],
                              cwd=directory,text=True,capture_output=True)
        check(result.returncode==0 and result.stdout.strip()==expected,'tool denial '+name+': '+result.stdout+result.stderr)
        check(result.stderr.count('WARN: tool call denied by headless agy — retrying once')==warnings,'tool denial '+name+' warning count')


def w():
    scenario=rows(('audit','11-20',1,'fail:page-missing:18'))
    x=Case('w',scenario,{'GEMINI_PDF_KEEP_AUDIT_DIR':1}).run();check_final_verified(x)
    chunk=x.audit['chunks'][1];audit=chunk['audit']
    check(audit['contradicted']==1 and audit['critical']==0 and audit['status']=='pass','complete page did not contradict the critical omission')
    check(audit['verdict']=='FAIL','raw model verdict was not retained as evidence')
    finding=audit['findings'][0]
    check(finding['page']==18 and finding['contradicted'] is True and finding.get('contradicted_by'),'discounted finding evidence was not retained')
    check(chunk['final_source']=='pro' and not chunk['repair']['attempted'] and not x.rows('repair'),'contradicted omission triggered repair')
    check(len(x.rows('audit','11-20'))==1 and not x.mathpix(),'contradicted omission dispatched an extra remedy')
    check(re.search(r'\bresidual=\d+ contradicted=1\b',x.stderr.splitlines()[-1]) is not None,'AUDIT summary lacks contradicted count after residual')
    evidence=read_json(x.out.with_suffix('.audit')/'chunk_11_20/det-0.json')['page_checks']['18']
    check(evidence['trigrams']>=40 and evidence['trigram_recall']>=.4 and evidence['missing_sentences']['count']==0,'complete-page fake did not establish the intended contradiction')

    dropped=Case('w-dropped',rows(('convert','11-20',1,'ok_drop_page:18'))+scenario,
                 {'GEMINI_PDF_KEEP_AUDIT_DIR':1}).run();check_final_verified(dropped)
    first=dropped.audit['chunks'][1]['attempts'][0]
    check(first['audit'].get('contradicted',0)==0 and not first['audit']['findings'][0].get('contradicted',False),'real missing page was discounted')
    check(first['audit']['critical']==1 and first['score']['pass'] is False,'real missing page did not fail audit and gate')
    # A whole fixture page contains enough absent sentences to trigger the
    # existing truncation remedy: fresh conversion before any edit repair.
    check(dropped.audit['chunks'][1]['repair']['attempted'] and len(dropped.rows('convert','11-20'))==2
          and dropped.audit['chunks'][1]['final_source']=='pro-reconvert','real missing page did not trigger fresh conversion')
    check('entire page 18 is missing' in (dropped.out.with_suffix('.audit')/'chunk_11_20/hints-0.txt').read_text(),'real omission did not reach remedy hints')
    missing=read_json(dropped.out.with_suffix('.audit')/'chunk_11_20/det-0.json')['page_checks']['18']
    check(missing['trigram_recall']<.4 and missing['missing_sentences']['count']>0,'page-deletion fake retained the missing page')


def unit(name,filename):
    root=RUN/name;root.mkdir()
    rc,out,err=run_proc([sys.executable,str(TESTS/filename)],dict(os.environ,TMPDIR=str(root),PYTHONDONTWRITEBYTECODE='1'),limit=35)
    (root/'stdout').write_text(out);(root/'stderr').write_text(err)
    check(rc==0,f'{filename} failed; log {root}/stderr: '+err[-1000:])
def T12():unit('T12','test_quality_chunk.py')
def T13():unit('T13','test_audit_tools.py')
def T19():unit('T19','test_mathpix.py')

def T16():
    x=Case('T16',FAIL_LAST).run();check_final_verified(x)
    paid=x.ledger();check([r['outcome'] for r in paid]==['dispatched','ok'],'reservation/outcome rows')
    check(paid[0]['dispatch_id']==paid[1]['dispatch_id'],'outcome dispatch id changed')
    check(set(paid[0])==set('utc_ts dispatch_id pdf_sha256 pdf_name pages n_pages mode origin est_usd outcome run_nonce'.split()),'ledger schema changed')
    check(paid[0]['n_pages']=='5' and paid[0]['origin']=='chunk' and float(paid[0]['est_usd'])==.125,'ledger charged wrong chunk')
    check(paid[0]['pdf_sha256']==hashlib.sha256(x.pdf.read_bytes()).hexdigest(),'ledger document identity')

def T17():
    x=Case('T17-auto',FAIL_LAST,{'GEMINI_PDF_MATHPIX_PAGE_CAP':0}).run().expect('partial').common()
    check(not x.mathpix() and x.audit['chunks'][2]['mathpix']['reason']=='mathpix-cap','cap 0 failed to disable automatic Mathpix')
    y=Case('T17-small',env={'GEMINI_PDF_MATHPIX_PAGE_CAP':0}).run('--force-mathpix').common(notices=0)
    check(not y.mathpix() and y.rc!=0,'explicit Mathpix bypassed zero cap')
    z=Case('T17-explicit').run('--force-mathpix','--mathpix-page-cap','25').common(notices=1)
    check(z.rc==0 and len(z.mathpix())==1 and z.mathpix()[0][1]=='25','explicit cap-sized Mathpix failed')

def T18():
    x=Case('T18');bad=x.root/'blocked';bad.write_text('not a directory');x.env['GEMINI_PDF_MATHPIX_LEDGER']=str(bad/'ledger.tsv')
    x.run('--force-mathpix','--mathpix-page-cap','25').common(notices=1)
    check(x.rc==0 and len(x.mathpix())==1,'explicit origins must continue under per-run cap if ledger unwritable')
    check('WARN' in x.stderr and 'ledger' in x.stderr.lower(),'unwritable ledger failure silent')

def T21():
    scenario=rows(('audit','11-20',1,'fail:x'),('audit','11-20',2,'fail:x'))+FAIL_LAST
    x=Case('T21-first',scenario,{'FAKE_MATHPIX_BEHAVIOUR':'fail'}).run().expect('partial').common()
    check(x.audit['mathpix']['pages_used']==15,'ambiguous first run must consume 15 reserved pages')
    check(sum(r['outcome']=='dispatched' for r in x.ledger())==2,'first-run reservation ledger')
    y=Case('T21-second',scenario);y.env['GEMINI_PDF_MATHPIX_LEDGER']=x.env['GEMINI_PDF_MATHPIX_LEDGER'];y.run().expect('partial').common()
    check(y.audit['chunks'][1]['mathpix']['reason']=='mathpix-cap','cross-run used pages ignored')
    check([r[1] for r in y.mathpix()]==['5'],'second run may dispatch only remaining 5 pages')
    check(sum(int(r['n_pages']) for r in y.ledger() if r['outcome']=='dispatched')==20,'cross-run ceiling exceeded')
    z=Case('T21-unreadable',FAIL_LAST);bad=z.root/'ledger-dir';bad.mkdir();z.env['GEMINI_PDF_MATHPIX_LEDGER']=str(bad)
    z.run().expect('partial').common()
    check(not z.mathpix(),'automatic origin must fail closed on unreadable ledger')
    check('WARN' in z.stderr and 'ledger' in z.stderr.lower(),'unreadable ledger not announced')


def build_fixture():
    directory=RUN/'fixture';directory.mkdir();shutil.copyfile(TESTS/'fixtures/synthetic_paper.tex',directory/'synthetic_paper.tex')
    generated=subprocess.check_output([sys.executable,str(TESTS/'fixtures/gen_fixture.py'),'--seed','20260904','--out',str(directory/'generated.tex')])
    check((directory/'generated.tex').read_bytes()==(directory/'synthetic_paper.tex').read_bytes(),'committed fixture differs from seeded generator')
    for attempt in range(2):
        r=subprocess.run(['pdflatex','-no-shell-escape','-interaction=nonstopmode','-halt-on-error','synthetic_paper.tex'],cwd=directory,capture_output=True,timeout=30)
        (directory/f'build-{attempt}.log').write_bytes(r.stdout+r.stderr);check(r.returncode==0,f'fixture build failed: {directory}')
    pdf=directory/'synthetic_paper.pdf'
    info=subprocess.check_output(['pdfinfo',str(pdf)]).decode();check(re.search(r'^Pages:\s+25$',info,re.M) is not None,'synthetic fixture is not 25 pages')
    src=subprocess.check_output(['pdftotext','-layout',str(pdf),'-'])
    tex=(directory/'synthetic_paper.tex').read_text()
    check(tex.count(r'\thanks{')==1 and tex.count(r'\footnote{')==11,'fixture must contain a title footnote and 11 body footnotes')
    check(tex.count(r'\section{')==8 and tex.count(r'\subsection{')==12,'fixture heading counts changed')
    check(tex.count(r'\begin{tabular}')==3 and tex.count(r'\bibitem{')==20,'fixture table/reference counts changed')
    tags=set(re.findall(rb'\((\d+)\)\s*$',src,re.M))
    check(len(tags)==40 and tex.count(r'\nonumber')==4,'fixture must have 40 numbered equations and 4 unnumbered align rows')
    digest=hashlib.md5(src).hexdigest();check(digest==(TESTS/'fixtures/synthetic_paper.textdigest').read_text().strip(),'fixture text digest changed: '+digest)
    (directory/'synthetic_paper.txt').write_bytes(src)
    return pdf


def main():
    global RUN,SCRIPT,FIXTURE,REAL_JQ
    check(os.environ.get('GEMINI_PDF_OFFLINE_ISOLATED')=='1','run through tests/run_offline_suite.sh')
    selected=sys.argv[1:] or NAMES
    check(all(name in NAMES for name in selected),'unknown scenario')
    REAL_JQ=shutil.which('jq');check(REAL_JQ is not None,'real jq is required for the transparent test shim')
    base=Path(os.environ.get('TMPDIR','/tmp'))/'gemini-pdf-tests';base.mkdir(parents=True,exist_ok=True)
    RUN=Path(tempfile.mkdtemp(prefix=time.strftime('%Y%m%d-%H%M%S')+'-',dir=base))
    shutil.copytree(SOURCE,RUN/'scripts');shutil.copyfile(TESTS/'fakes/mathpix_extract.py',RUN/'scripts/mathpix_extract.py')
    shutil.copytree(TESTS/'fakes',RUN/'fakes')
    SCRIPT=RUN/'scripts/pdf_to_markdown.sh';FIXTURE=build_fixture()
    suite_started=time.monotonic()
    results={}
    def execute(name):
        started=time.monotonic()
        try:globals()[name]();return True,'',time.monotonic()-started
        except Exception as exc:return False,f'{exc} [scratch {RUN}]',time.monotonic()-started
    # Deadline/watchdog scenarios f, f2, f3, f4, f5, g3, l run serially after
    # the six-worker pool; k2 retains its own intentional two-worker lock test.
    serial=[name for name in selected if name in ('f','f2','f3','f4','f5','g3','l')]
    # Start scenarios with several sequential conversions first; assertions
    # and printed results still follow the caller's requested order.
    heavy=[name for name in ('T21','d','v','g2','k2') if name in selected and name not in serial]
    dispatch_order=heavy+[name for name in selected if name not in serial and name not in heavy]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures={name:pool.submit(execute,name) for name in dispatch_order}
        for name in dispatch_order:results[name]=futures[name].result()
    for name in serial:results[name]=execute(name)
    for name in selected:
        ok,err,elapsed=results[name]
        print(('PASS '+name) if ok else ('FAIL '+name+': '+err),flush=True)
    for name in selected:print(f'WALL {name} {results[name][2]:.3f}s')
    print(f'WALL suite {time.monotonic()-suite_started:.3f}s (excluding fixture build)')
    count=sum(results[name][0] for name in selected)
    print(f'SUITE: PASS {count}/{len(selected)}' if count==len(selected) else f'SUITE: FAIL {len(selected)-count}/{len(selected)}')
    return int(count!=len(selected))
if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:print('FAIL setup: '+str(exc));print('SUITE: FAIL 1/1');sys.exit(1)
