"""判例的草稿目录工厂（CONTRACT §58.3 hygiene 门的 `mkdtemp:` 规则；issue #436）。

``tests/`` 里只有两处准直接调 ``tempfile.mkdtemp``：``tests/__init__.py`` 铸整次 run 的
沙箱根（退出时整树删），和这里。别处一律 ``scratch_dir(self, prefix="...")``——目录
是真的，寿命是这条判例（或传 ``cls`` 时这个 TestCase 类）的 cleanup 阶段。2026-09-19
owner 机器的 $TMPDIR 里堆着 215k 个、5.6 GB 的判例草稿目录，每个前缀都对得上一处
忘了 cleanup 的 mkdtemp——这就是把 mkdtemp 收进一个门的原因。
判例：tests/test_scratch_dir_removed_on_cleanup.py。
"""
import shutil
import tempfile


def scratch_dir(case, prefix="", suffix="", dir=None):
    """铸一个真目录，登记在 ``case`` 的 cleanup 里整树删掉，返回 str（同 mkdtemp）。

    ``case`` 是 TestCase 实例（→ ``addCleanup``）或 TestCase 类——``setUpClass`` 里传
    ``cls``（→ ``addClassCleanup``，整个类跑完再删）。关键字与 ``tempfile.mkdtemp`` 同名同义。
    """
    path = tempfile.mkdtemp(suffix=suffix, prefix=prefix, dir=dir)
    register = case.addClassCleanup if isinstance(case, type) else case.addCleanup
    register(shutil.rmtree, path, ignore_errors=True)
    return path
