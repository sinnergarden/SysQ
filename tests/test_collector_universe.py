from unittest.mock import patch

import pandas as pd

from qsys.data.collector import TushareCollector


@patch.object(TushareCollector, "__init__", lambda self: None)
def test_get_universe_supports_csi800_with_member_fallback():
    collector = TushareCollector()
    collector.store = None
    collector.pro = type("Pro", (), {"index_member": object()})()
    collector._fetch_with_retry = lambda *args, **kwargs: pd.DataFrame(
        {"con_code": ["000001.SZ", "000002.SZ", "000003.SZ"]}
    )
    collector.get_index_weights = lambda index_code: pd.DataFrame(
        {"con_code": ["000001.SZ"]}
    )

    codes = collector.get_universe("csi800")

    assert codes == ["000001.SZ", "000002.SZ", "000003.SZ"]
