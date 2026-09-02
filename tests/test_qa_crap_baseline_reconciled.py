"""§58.2/§58.4：qa/crap_baseline.txt 必须是 canonical linux 腿收的账。

f2a54c1 审查 blocker 2：首铸账本里残留了 8 条 darwin 收的分——其中
ensure_repo 登记 95.7 而 canonical 实测 22.5，等于留了 73 分的静默回归
窗口；compute_target_kind 在 canonical 上已达标却仍挂账（limbo 带把
清账漏了）。对账 = 从 CI qa-report artifact 拷回（run 33569581546，
两次 linux run 逐字节一致），但对账自身也受 §58.4 base 差分管辖——
只许缩，所以 _check_launchd 保留更紧的 17（canonical 17.1，0.1 差
由 [crap].tolerance 吸收），其余 7 条取 canonical 值。本判例钉死这批
值只许缩、已划掉的键不许回账、账本条数不许涨回出生值以上——darwin
收的松账从此回不来。
"""
import os
import sys
import unittest

_QA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "qa")
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)

import qa_common  # noqa: E402

_LEDGER = os.path.join(qa_common.REPO_ROOT, "qa", "crap_baseline.txt")

# canonical linux 腿（CI qa-gates，Python 3.13）对这 8 条实测的分——
# blocker 2 点名的 5 条 + 同次对账顺手修正的 3 条亚容差漂移。
_CANONICAL_CEILING = {
    "act/executor.py::ensure_repo": 22.5,      # 曾登记 95.7（darwin）
    "act/doctor.py::_pid_alive": 10.0,         # 曾登记 24.3
    "act/executor.py::dispatch": 48.2,         # 曾登记 51.1
    "act/doctor.py::_check_daemon_claude": 19.0,   # 曾登记 19.6
    "act/doctor.py::_check_launchd": 17.1,
    "act/doctor.py::_check_systemd": 13.0,
    "act/doctor.py::_actd_alive": 7.0,
}

# 出生时（对账后）的账本条数；P3 只会往下削。
_BIRTH_SIZE = 369


class CrapLedgerReconciledTestCase(unittest.TestCase):
    def setUp(self):
        self.ledger = qa_common.load_ledger(_LEDGER)

    def test_reconciled_entries_never_regain_darwin_headroom(self):
        # 键还在账上就必须 ≤ canonical 实测值（划掉 = 缩，更好）。
        for key, canonical in _CANONICAL_CEILING.items():
            listed = self.ledger.get(key)
            if listed is not None:
                self.assertLessEqual(listed, canonical, key)

    def test_canonically_clean_function_is_not_enrolled(self):
        # compute_target_kind 在 canonical 上 5.6 ≤ 阈值——干净的函数
        # 不许挂账（limbo 带是给抖动的，不是给对账残渣的）。
        self.assertNotIn("act/executor.py::compute_target_kind", self.ledger)

    def test_ledger_never_regrows_past_its_birth_size(self):
        self.assertLessEqual(len(self.ledger), _BIRTH_SIZE)


if __name__ == "__main__":
    unittest.main()
