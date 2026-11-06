#!/usr/bin/env python3
"""Deterministic, explicitly paginated fixture; PDF metadata is not an oracle."""
import argparse
import random
from pathlib import Path

VOCAB = '''ability absence account action activity addition adjustment advantage agreement allocation amount analysis approach area argument aspect assessment assumption attention authority average balance basis benefit boundary capital category cause certainty change character choice circumstance claim class coefficient combination comparison competition component conclusion condition confidence consequence constant constraint construction consumption content context contract control convention cost country coverage criterion data decision definition degree demand density dependence description design detail determination development difference dimension direction distribution division duration effect efficiency element employment energy environment equality error estimate evaluation event evidence example exception exchange existence expectation experience experiment explanation factor feature field finance finding firm form framework function gain gap growth group household identity implication incentive income increase independence index indicator individual industry influence information input instance institution instrument interaction interest interpretation interval investment issue item knowledge labour law level limit line location loss magnitude market measure mechanism method model moment movement name nature norm number observation occupation outcome output parameter participation pattern period place point policy population position precision prediction preference price principle probability procedure process product production projection property proportion proposal quantity question range rate ratio reaction reason record reduction reference reform region regulation relation relationship remainder report representation requirement research residual resource response restriction result return revision risk role rule sample scale scenario scheme scope section selection sensitivity sequence service set share shift signal similarity size skill source space specification stability standard state statistic step stock strategy strength structure subject substitution supply support system table task technique tendency term test theory threshold time tool total trade treatment trend type uncertainty understanding unit value variable variation version view volume wage weakness weight welfare willingness work world access accuracy advantage agreement alternative application appointment arrangement association background barrier behaviour benchmark benefit capacity channel classification collection communication concentration concept connection consideration consistency contribution correction correlation criterion definition difference direction discipline economy education elasticity equilibrium equivalent expansion expertise explanation feedback flexibility foundation frequency geography history hypothesis identification illustration importance innovation intensity limitation literature maintenance membership motivation opportunity organization output partnership performance persistence phenomenon plausibility practice pressure priority productivity provision publication rationale recovery relevance reliability robustness satisfaction schedule security simplicity situation sophistication solution statistic technology tradition transformation transition transparency usefulness validity verification vision volatility'''.split()[:300]

def generate(seed):
    rng = random.Random(seed)
    out = [r'\documentclass[10pt]{article}', r'\usepackage[margin=0.85in]{geometry}',
           r'\usepackage{amsmath,amssymb,booktabs}', r'\setlength{\parskip}{8pt}',
           r'\setlength{\parindent}{0pt}', r'\title{Synthetic Econometric Pipeline Paper\thanks{This explanatory footnote preserves the local argument and the supporting interpretation without changing its assumptions.}}',
           r'\author{Offline Fixture Author}', r'\date{2026}', r'\begin{document}']
    sections = {1:'Introduction',4:'Economic Environment',7:'Identification',10:'Estimation',13:'Inference',16:'Simulation',19:'Empirical Results',22:'Conclusion'}
    for page in range(1,26):
        if page == 1:
            out += [r'\maketitle',r'\begin{abstract}', 'This synthetic paper provides reproducible evidence for deterministic pipeline checks and contains no unpublished research material.',r'\end{abstract}']
        if page in sections: out.append(r'\section{'+sections[page]+'}')
        if page <= 12: out.append(r'\subsection{'+['Baseline','Assumptions','Evidence','Specification','Mechanism','Design','Comparison','Interpretation','Application','Sensitivity','Selection','Extensions'][page-1]+'}')
        for para in range(2):
            sentences = []
            for _ in range(3):
                words = rng.choices(VOCAB,k=32)
                sentences.append(' '.join(words).capitalize()+'.')
            out.append(' '.join(sentences)+'\n')
        out.append(f'The recorded sample includes {1000+page} households and {2000+page} firms with {3000+page} observations. The benchmark outcome is {4000+page} units and the comparison outcome is {5000+page} units.')
        if 2 <= page <= 12: out.append(r'\footnote{This explanatory footnote preserves the local argument and the supporting interpretation without changing its assumptions.}')
        if page <= 20:
            if page <= 4:
                out += [r'\begin{align}',rf'y_{{{page}}} &= {100+page} x_{{{page}}} + {200+page} \\',r' &= \alpha + \beta x \nonumber \\',f'z_{{{page}}} &= {300+page} x_{{{page}}} + {400+page}',r'\end{align}']
            else:
                for j in range(2): out += [r'\begin{equation}',f'y_{{{page}}} = {100+page+j} x_{{{page}}} + {200+page+j}',r'\end{equation}']
        if page in (5,14,21):
            out += [r'\begin{center}',r'\textbf{Table '+str((5,14,21).index(page)+1)+r'. Sample moments}',r'\begin{tabular}{lrr}',r'\toprule',r'Group & Mean & Count \\',r'\midrule',r'Baseline & 0.019 & 521 \\',r'Comparison & 0.001 & 932 \\',r'\bottomrule',r'\end{tabular}',r'\end{center}']
        if page == 25:
            out += [r'\begin{thebibliography}{99}']
            for n in range(1,21):
                out.append(r'\bibitem{ref'+str(n)+'} Author '+str(n)+f' ({1980+n}). Synthetic reference title {n}. Journal of Fixture Economics, {30+n}, {100+n}--{110+n}.')
            out += [r'\end{thebibliography}']
        if page != 25: out.append(r'\newpage')
    out += [r'\end{document}']
    return '\n'.join(out)+'\n'

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--seed',type=int,default=20260904);p.add_argument('--out',type=Path,default=Path(__file__).with_name('synthetic_paper.tex'))
    a=p.parse_args();a.out.write_text(generate(a.seed),encoding='utf-8')
