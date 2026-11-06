#!/usr/bin/env python3
"""Synthetic offline checks; invoke through scripts/run_offline_tests.py."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'

PROVIDER = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
name=Path(sys.argv[0]).name
args=sys.argv[1:]
with open(os.environ['PROVIDER_LOG'],'a') as log:
    log.write(json.dumps({'name':name,'args':args,'cwd':os.getcwd(),
        'env':{k:v for k,v in os.environ.items() if k.startswith(('GROK_CLAUDE_','GROK_CURSOR_','CLAUDE_DELEGATE_'))}})+'\n')
if '--help' in args:
    print('-p --single --model --output-last-message --skip-git-repo-check --print --print-timeout --output-format --effort --tools --no-session-persistence --strict-mcp-config --permission-mode --prompt-file --deny --no-subagents --disable-web-search --reasoning-effort --max-turns --verbatim --no-plan')
    sys.exit(0)
if '--version' in args:
    print('synthetic-1.0'); sys.exit(0)
if name=='claude' and args==['--max-turns']:
    print('argument missing',file=sys.stderr); sys.exit(2)
if name=='codex':
    if os.getenv('SYNTH_CODEX_MODE')=='fail':
        print('ERROR: synthetic transport failure',file=sys.stderr); sys.exit(3)
    body={'semantic':'STATUS: FAILED — synthetic semantic failure','malformed':'VERDICT: CORRECTNESS — invalid vocabulary'}.get(os.getenv('SYNTH_CODEX_MODE'),'VERDICT: CORRECT — synthetic review')
    Path(args[args.index('--output-last-message')+1]).write_text(body+'\n')
    sys.exit(0)
if name=='grok':
    if os.getenv('SYNTH_GROK_MODE')=='empty': sys.exit(0)
    print(json.dumps({'sessionId':'synthetic','stopReason':'done','text':'VERDICT: CORRECT — synthetic optional review'})); sys.exit(0)
if name=='claude':
    print(json.dumps({'subtype':'success','is_error':False,'session_id':'synthetic','num_turns':1,'total_cost_usd':0,'result':'VERDICT: CORRECT — synthetic optional review'})); sys.exit(0)
print('unexpected provider command',file=sys.stderr); sys.exit(97)
'''

CONVERTER = r'''#!/bin/bash
set -euo pipefail
printf '%s\n' "$0" >> "$CONVERTER_LOG"
printf 'Synthetic conversion\n' > "$2"
if [[ -n "${SYNTH_SYNC_KEY:-}" && -n "${GEMINI_PDF_REFERENCE_DIR:-${REFERENCE_DIR:-}}" ]]; then
    ref="${GEMINI_PDF_REFERENCE_DIR:-${REFERENCE_DIR:-}}"
    mkdir -p "$ref/md"
    cp "$2" "$ref/md/$SYNTH_SYNC_KEY.md"
fi
printf 'AUDIT: status=disabled chunks=1\n' >&2
printf '%s\n' "$2"
'''


class PublicContract(unittest.TestCase):
    def setUp(self):
        if not os.environ.get('OFFLINE_TEST_ROOT'):
            self.fail('Use the isolated repository offline test launcher')
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'], prefix='delegate-contract-')
        self.case = Path(self.temp.name).resolve()
        self.bin = self.case / 'bin'
        self.bin.mkdir()
        # Preserve only local allowlisted tools from the launcher's already restricted PATH.
        for folder in os.environ['PATH'].split(os.pathsep):
            for path in Path(folder).iterdir():
                if path.name in ('codex','agy','grok','claude'):
                    continue
                dest=self.bin/path.name
                if not dest.exists():
                    dest.symlink_to(path)
        for name in ('codex','grok','claude'):
            path=self.bin/name; path.write_text(PROVIDER); path.chmod(0o755)
        # The agy fake is synthetic and rejects/records every command it handles.
        (self.bin/'agy').symlink_to(ROOT/'tests/fakes/agy')
        (self.bin/'samples').symlink_to(ROOT/'tests/fakes/samples')
        self.env={key:value for key,value in os.environ.items() if key in
                  ('LANG','LC_ALL','REAL_PYTHON3','OFFLINE_TEST_ROOT','OFFLINE_BLOCKED_LOG','PYTHONDONTWRITEBYTECODE')}
        self.env.update(PATH=str(self.bin), HOME=str(self.case/'home'), TMPDIR=str(self.case/'tmp'),
            XDG_CONFIG_HOME=str(self.case/'config'), XDG_CACHE_HOME=str(self.case/'cache'),
            CODEX_HOME=str(self.case/'codex-home'), CLAUDE_CONFIG_DIR=str(self.case/'claude-home'),
            REFERENCE_DIR=str(self.case/'reference'), DEEPSEEK_DELEGATE_LEDGER=str(self.case/'ledger.tsv'),
            PROVIDER_LOG=str(self.case/'providers.jsonl'), FAKE_AGY_LEDGER=str(self.case/'agy.tsv'),
            FAKE_AGY_PRINT_TIMEOUT_LOG=str(self.case/'timeouts.tsv'),
            FAKE_AGY_PRINT_REPLY='VERDICT: CORRECT — synthetic review\n',
            CODEX_TIMEOUT='15',ANTIGRAVITY_TIMEOUT='15',CODEX_CAPACITY_RETRIES='0',
            CONVERTER_LOG=str(self.case/'converter.log'))
        for key in ('HOME','TMPDIR','XDG_CONFIG_HOME','XDG_CACHE_HOME','CODEX_HOME','CLAUDE_CONFIG_DIR','REFERENCE_DIR'):
            Path(self.env[key]).mkdir()
        for name in ('codex','agy','grok','claude','curl'):
            self.assertEqual(shutil.which(name,path=self.env['PATH']),str(self.bin/name))
        self.outputs=[str(self.case/name) for name in ('codex.md','grok.md','gemini.md','claude.md')]

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self,script,*args,changes=None,timeout=35):
        env=dict(self.env,**(changes or {}))
        command=['/bin/bash',str(script),*args]
        process=subprocess.Popen(command,env=env,cwd=self.case,stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,text=True,start_new_session=True)
        try:
            stdout,stderr=process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGTERM)
            try:
                stdout,stderr=process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL)
                stdout,stderr=process.communicate()
            self.fail('offline subprocess timeout; stdout='+stdout+'; stderr='+stderr)
        return subprocess.CompletedProcess(command,process.returncode,stdout,stderr)

    def review(self,changes=None,args=None):
        return self.invoke(SCRIPTS/'ask_both.sh',*(args or ['Synthetic review',self.outputs[0],'',self.outputs[2]]),changes=changes)

    def calls(self):
        file=Path(self.env['PROVIDER_LOG'])
        return [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []

    def no_dispatch(self,result):
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.calls(),[])
        self.assertFalse(Path(self.env['FAKE_AGY_LEDGER']).exists())
        self.assertTrue(all(not Path(path).exists() for path in self.outputs))

    def test_default_actual_legs_and_fresh_output(self):
        for path in self.outputs: Path(path).write_text('VERDICT: CORRECT — stale\n')
        result=self.review()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.splitlines(),[self.outputs[0],self.outputs[2]])
        names={row['name'] for row in self.calls()}
        self.assertEqual(names,{'codex'})
        self.assertIn('synthetic review',Path(self.outputs[0]).read_text())
        self.assertNotIn('stale',Path(self.outputs[2]).read_text())
        agy=Path(self.env['FAKE_AGY_LEDGER']).read_text()
        self.assertIn('model-probe',agy)
        self.assertIn('gemini-4-argon-high',agy)
        args=self.calls()[0]['args']
        self.assertIn('gpt-6-astra',args)
        self.assertIn('model_reasoning_effort=max',args)
        self.assertNotIn('fallback',Path(self.outputs[0]).read_text())

    def test_optional_grok_uses_real_wrapper_tool_guards(self):
        result=self.review(changes={'INCLUDE_GROK':'1','GROK_DELEGATE_AGENTIC':'1','GROK_DELEGATE_WORKDIR':str(self.case)},
                           args=['Synthetic review',*self.outputs[:3]])
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.splitlines(),self.outputs[:3])
        calls=[r for r in self.calls() if r['name']=='grok' and '--prompt-file' in r['args']]
        self.assertEqual(len(calls),1)
        self.assertIn('--deny',calls[0]['args']); self.assertIn('--no-subagents',calls[0]['args'])
        self.assertTrue(all(value=='0' for value in calls[0]['env'].values()))

    def test_optional_claude_never_counted(self):
        config=Path(self.env['HOME'])/'.claude-delegate'; config.mkdir()
        token=config/'oauth_token'; token.write_text('synthetic-token\n'); token.chmod(0o600)
        result=self.review(changes={'INCLUDE_CLAUDE':'1','CLAUDE_DELEGATE_AGENTIC':'1','CLAUDE_DELEGATE_RESUME':'ignored'},
                           args=['Synthetic review',self.outputs[0],'',self.outputs[2],self.outputs[3]])
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('never counted as independent',result.stderr)
        calls=[r for r in self.calls() if r['name']=='claude' and '--print' in r['args']]
        self.assertEqual(len(calls),1)
        self.assertIn('--tools',calls[0]['args']); self.assertIn('--no-session-persistence',calls[0]['args'])
        self.assertNotIn('--resume',calls[0]['args'])

    def test_retired_controls_refuse_before_dispatch(self):
        for changes in ({'DEEPSEEK_RESERVE_FOR':'grok'},{'DEEPSEEK_RESERVE_FOR':'anything'},
                        {'DEEPSEEK_SHADOW':'1'},{'DEEPSEEK_SHADOW':'yes'}):
            with self.subTest(changes=changes): self.no_dispatch(self.review(changes=changes))

    def test_ambiguous_arguments_refuse_before_dispatch(self):
        for changes in ({},{'INCLUDE_GROK':'1'}):
            with self.subTest(changes=changes):
                self.no_dispatch(self.review(changes=changes,args=['Synthetic review',*self.outputs[:2]]))

    def test_paid_or_wrong_family_review_refuses(self):
        for changes in ({'CODEX_MODEL':'deepseek/test'}, {'ANTIGRAVITY_MODEL':'claude-opus-5-5-high'},
                        {'INCLUDE_GROK':'1','GROK_DELEGATE_BACKEND':'api'}):
            args=['Synthetic review',*self.outputs[:3]] if changes.get('INCLUDE_GROK') else None
            with self.subTest(changes=changes): self.no_dispatch(self.review(changes=changes,args=args))

    def test_duplicate_outputs_refuse(self):
        self.no_dispatch(self.review(args=['Synthetic review',self.outputs[0],'',self.outputs[0]]))

    def test_requested_transport_failure_fails_round(self):
        result=self.review(changes={'SYNTH_CODEX_MODE':'fail'})
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertIn('STATUS: FAILED',Path(self.outputs[0]).read_text())

    def test_semantic_failure_fails_round(self):
        for mode in ('semantic','malformed'):
            with self.subTest(mode=mode):
                result=self.review(changes={'SYNTH_CODEX_MODE':mode})
                self.assertNotEqual(result.returncode,0,result.stderr)
                self.assertIn('exit=1',result.stderr)

    def test_optional_provider_missing_does_not_fail_preflight(self):
        (self.bin/'grok').unlink(); (self.bin/'claude').unlink()
        result=self.invoke(SCRIPTS/'preflight.sh')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Grok optional CLI missing',result.stderr)
        self.assertIn('Claude optional CLI missing',result.stderr)
        self.assertTrue(all('--help' in row['args'] for row in self.calls()))
        self.assertTrue(all(row.split('\t')[2]=='help' for row in Path(self.env['FAKE_AGY_LEDGER']).read_text().splitlines()))

    def test_required_missing_and_required_flags_fail_offline(self):
        result=self.invoke(SCRIPTS/'preflight.sh','--offline',changes={'FAKE_AGY_HELP':'nomodel'})
        self.assertNotEqual(result.returncode,0)
        self.assertIn('missing flag --model',result.stderr)
        (self.bin/'codex').unlink()
        result=self.invoke(SCRIPTS/'preflight.sh','--offline')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Codex CLI missing',result.stderr)

    def test_relocated_converter_and_override(self):
        for label in ('marketplace/cache/package/skills','manual/skills'):
            with self.subTest(layout=label):
                skills=self.case/label
                scripts=skills/'delegate/scripts'; scripts.mkdir(parents=True)
                for name in ('ask_pdf2md.sh','preflight.sh'):
                    shutil.copyfile(SCRIPTS/name,scripts/name)
                sibling=skills/'gemini-pdf/scripts/pdf_to_markdown.sh'; sibling.parent.mkdir(parents=True)
                sibling.write_text(CONVERTER)
                fallback=Path(self.env['HOME'])/'.claude/skills/gemini-pdf/scripts/pdf_to_markdown.sh'
                fallback.parent.mkdir(parents=True,exist_ok=True); fallback.write_text('exit 99\n')
                document=self.case/'synthetic.pdf'; document.write_bytes(b'%PDF-synthetic-offline')
                result=self.invoke(scripts/'ask_pdf2md.sh',str(document),str(self.case/'converted.md'),
                                   changes={'GEMINI_PDF_MATHPIX_PAGE_CAP':'0'})
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('unaudited',result.stdout)
                resolved=Path(self.env['CONVERTER_LOG']).read_text().splitlines()[-1]
                self.assertEqual(Path(resolved).resolve(),sibling.resolve())
                preflight=self.invoke(scripts/'preflight.sh','--offline')
                self.assertEqual(preflight.returncode,0,preflight.stderr)
                self.assertIn('gemini-pdf/scripts/pdf_to_markdown.sh',preflight.stderr)
                explicit=self.case/'explicit-converter.sh'; explicit.write_text(CONVERTER)
                result=self.invoke(scripts/'ask_pdf2md.sh',str(document),str(self.case/'override.md'),
                    changes={'PDF2MD_CONVERTER':str(explicit),'GEMINI_PDF_MATHPIX_PAGE_CAP':'0'})
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(Path(self.env['CONVERTER_LOG']).read_text().splitlines()[-1],str(explicit))
                preflight=self.invoke(scripts/'preflight.sh','--offline',changes={'GEMINI_PDF_SCRIPT':str(explicit)})
                self.assertIn(str(explicit),preflight.stderr)

    def test_process_group_cancel_stops_descendant(self):
        scripts=self.case/'fake-scripts'; scripts.mkdir()
        (scripts/'ask_codex.sh').write_text('#!/bin/bash\nsleep 0.5\nexit 3\n')
        (scripts/'ask_antigravity.sh').write_text('#!/bin/bash\nperl -MPOSIX -e \'POSIX::setsid(); exec \"sleep\", \"20\"\' &\nprintf "%s\\n" "$!" > "$CHILD_PID_FILE"\nwait\n')
        pid_file=self.case/'child.pid'
        start=time.monotonic()
        result=self.review(changes={'DELEGATE_DIR':str(scripts),'CHILD_PID_FILE':str(pid_file)})
        self.assertNotEqual(result.returncode,0)
        self.assertLess(time.monotonic()-start,5)
        pid=int(pid_file.read_text())
        probe=subprocess.run(['ps','-p',str(pid),'-o','stat='],env=self.env,capture_output=True,text=True)
        self.assertTrue(probe.returncode!=0 or probe.stdout.strip().startswith('Z'),probe.stdout)

    def test_pdf_sync_status_uses_explicit_preferred_destination(self):
        document=self.case/'synthetic.pdf'; document.write_bytes(b'%PDF-synthetic-offline')
        converter=self.case/'converter.sh'; converter.write_text(CONVERTER)
        output=self.case/'conversion.md'
        preferred=self.case/'preferred-reference'
        result=self.invoke(SCRIPTS/'ask_pdf2md.sh',str(document),str(output),'--bib-key','example2026',
            changes={'PDF2MD_CONVERTER':str(converter),'GEMINI_PDF_REFERENCE_DIR':str(preferred),
                     'SYNTH_SYNC_KEY':'example2026','GEMINI_PDF_MATHPIX_PAGE_CAP':'0'})
        self.assertEqual(result.returncode,0,result.stderr)
        envelope=output.with_suffix('.status.md').read_text()
        self.assertIn('VAULT: synced',envelope)
        self.assertIn(str(preferred/'md/example2026.md'),envelope)
        self.assertNotIn(str(Path(self.env['REFERENCE_DIR'])/'md/example2026.md'),envelope)
        result=self.invoke(SCRIPTS/'ask_pdf2md.sh',str(document),str(output),'--bib-key','example2026',
            changes={'PDF2MD_CONVERTER':str(converter),'REFERENCE_DIR':'','GEMINI_PDF_REFERENCE_DIR':'',
                     'GEMINI_PDF_MATHPIX_PAGE_CAP':'0'})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('VAULT: not configured',output.with_suffix('.status.md').read_text())
        self.assertFalse((Path(self.env['HOME'])/'research/reference').exists())

    def test_grok_empty_success_returns_failure(self):
        result=self.invoke(SCRIPTS/'ask_grok.sh','Synthetic review',self.outputs[1],
                           changes={'SYNTH_GROK_MODE':'empty'})
        self.assertNotEqual(result.returncode,0,result.stderr)
        report=Path(self.outputs[1]).read_text()
        self.assertIn('STATUS: FAILED',report)
        self.assertIn('exit=1',report)

    def test_preserved_guards_in_source(self):
        codex=(SCRIPTS/'ask_codex.sh').read_text()
        self.assertIn('CREDIT_FALLBACK=off',codex)
        self.assertIn('_no_tool_calls',codex)
        self.assertIn('env -u OPENROUTER_API_KEY -u DEEPSEEK_DELEGATE_KEY_FILE',codex)
        deepseek=(SCRIPTS/'ask_deepseek.sh').read_text()
        for guard in ('CEILING_USD="10"','data_collection','_preset_marker_matches','CONTRACT-DRIFT'):
            self.assertIn(guard,deepseek)
        grok=(SCRIPTS/'ask_grok.sh').read_text()
        self.assertIn('GROK_CLAUDE_MCPS_ENABLED=0',grok)
        self.assertIn('--deny',grok)

    def test_deepseek_synthetic_security_fixtures(self):
        result=self.invoke(SCRIPTS/'ask_deepseek.sh',changes={'DEEPSEEK_DELEGATE_SELFTEST':'1'},timeout=300)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('SELFTEST: PASS',result.stdout)


if __name__=='__main__':
    unittest.main(verbosity=2)
