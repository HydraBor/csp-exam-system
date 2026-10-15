#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑一遍包内的全部自测（纯逻辑、不需要 docker、可以随时跑）。

    python3 tools/run_selftests.py
"""

from __future__ import annotations

import os
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.join(os.path.dirname(HERE), "tests")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, help='保存逐套结果与计数（不含业务数据）')
    args = parser.parse_args()
    tests = sorted(f for f in os.listdir(TESTS) if f.startswith("selftest_") and f.endswith(".py"))
    if not tests:
        print("没有找到自测脚本：", TESTS)
        return 1
    ok = fail = total = 0
    reports = []
    for name in tests:
        module = 'csp_exam.tests.' + name.removesuffix('.py')
        p = subprocess.run([sys.executable, '-X', 'utf8', '-m', module],
                           cwd=os.path.dirname(os.path.dirname(HERE)),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        nums = [l for l in out.splitlines() if "结果" in l]
        line = nums[-1].strip() if nums else next(
            (l.strip() for l in out.splitlines() if re.match(r'Ran \d+ tests?', l)), '')
        counted = re.search(r'(\d+)\s*项通过', line) or re.search(r'Ran (\d+) tests?', line)
        count = int(counted[1]) if counted else None
        reports.append({'suite': name, 'ok': p.returncode == 0, 'count': count,
                        'summary': line, 'skipped': bool(re.search(r'skipped=\d+', out)),
                        **({'failure': out[-6000:]} if p.returncode else {})})
        if p.returncode == 0:
            ok += 1
            print("  [通过] %-28s %s" % (name, line))
            total += count or 0
        else:
            fail += 1
            print("  [失败] %-28s" % name)
            print("\n".join("      " + l for l in out.strip().splitlines()[-6:]))
    print()
    print("自测：%d 套通过，%d 套失败" % (ok, fail))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'passed_suites': ok, 'failed_suites': fail,
            'counted_cases_and_legacy_checks': total, 'suites': reports},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
