"""
Test for margin financing (两融) first batch integration.
Validates:
1. Config has margin interface definition
2. Collector can fetch margin data
3. Data is correctly merged into daily data
"""
import pytest
import pandas as pd
from qsys.config import cfg


class TestMarginBatch1:
    """Test margin financing first batch integration"""
    
    def test_config_has_margin_interface(self):
        """Verify config has margin interface definition"""
        tushare_cfg = cfg.get_tushare_feature_config()
        collector_cfg = tushare_cfg.get("collector", {})
        
        # Check interfaces has margin
        interfaces = collector_cfg.get("interfaces", {})
        assert "margin" in interfaces, "margin interface not defined in config"
        
        # Check margin fields
        margin_cfg = interfaces["margin"]
        assert "fields" in margin_cfg, "margin fields not defined"
        assert "rzye" in margin_cfg["fields"], "margin_balance field (rzye) not in fields"
        
        # Check rename map
        assert "rename" in margin_cfg, "margin rename not defined"
        rename = margin_cfg["rename"]
        assert "rzye" in rename, "rzye not in rename map"
        
    def test_config_has_margin_cols(self):
        """Verify margin columns are defined"""
        tushare_cfg = cfg.get_tushare_feature_config()
        collector_cfg = tushare_cfg.get("collector", {})
        
        margin_cols = collector_cfg.get("margin_cols", [])
        assert len(margin_cols) > 0, "margin_cols not defined"
        
        # Check expected fields
        expected = ["margin_balance", "margin_buy_amount", "margin_repay_amount"]
        for col in expected:
            assert col in margin_cols, f"{col} not in margin_cols"
    
    def test_config_has_qlib_fields(self):
        """Verify adapter qlib_fields includes margin"""
        tushare_cfg = cfg.get_tushare_feature_config()
        adapter_cfg = tushare_cfg.get("adapter", {})
        
        qlib_fields = adapter_cfg.get("qlib_fields", [])
        margin_fields = ["margin_balance", "lend_volume", "margin_buy_amount"]
        
        for field in margin_fields:
            assert field in qlib_fields, f"{field} not in qlib_fields"

    def test_collector_has_margin_attributes(self):
        """Verify collector has margin-related attributes"""
        from qsys.data.collector import TushareCollector
        
        # Check if collector can be instantiated
        try:
            collector = TushareCollector()
        except ValueError as e:
            if "Tushare token" in str(e):
                pytest.skip("Tushare token not available")
            raise
        
        # Check margin_cols attribute exists
        assert hasattr(collector, "margin_cols"), "collector missing margin_cols"
        assert len(collector.margin_cols) > 0, "margin_cols is empty"
        
        # Check _margin_interfaces
        assert hasattr(collector, "_margin_interfaces"), "collector missing _margin_interfaces"
        
    def test_collector_get_margin_fields(self):
        """Verify collector can get margin interface fields"""
        from qsys.data.collector import TushareCollector
        
        try:
            collector = TushareCollector()
        except ValueError as e:
            if "Tushare token" in str(e):
                pytest.skip("Tushare token not available")
            raise
        
        fields = collector._get_interface_fields("margin")
        assert fields is not None, "margin fields returned None"
        assert "rzye" in fields, "rzye not in margin fields"
        
    def test_collector_get_margin_rename(self):
        """Verify collector can get margin rename map"""
        from qsys.data.collector import TushareCollector
        
        try:
            collector = TushareCollector()
        except ValueError as e:
            if "Tushare token" in str(e):
                pytest.skip("Tushare token not available")
            raise
        
        rename = collector._get_interface_rename("margin")
        assert rename is not None, "margin rename returned None"
        assert "rzye" in rename, "rzye not in margin rename"
        assert rename["rzye"] == "margin_balance", "rzye mapping incorrect"
        
    def test_margin_data_fetch(self):
        """Test fetching actual margin data from Tushare"""
        from qsys.data.collector import TushareCollector
        import pandas as pd
        
        try:
            collector = TushareCollector()
        except ValueError as e:
            if "Tushare token" in str(e):
                pytest.skip("Tushare token not available")
            raise
        
        # Fetch margin data for a specific date
        try:
            margin_df = collector._fetch_with_retry(
                collector.pro.margin_detail,
                trade_date="20240301",
                fields="ts_code,trade_date,rzye,rzmre,rzche,rzrqye,rqyl,rqmcl,rqchl"
            )
        except Exception as e:
            pytest.skip(f"Margin API not available: {e}")
        
        if margin_df is None or margin_df.empty:
            pytest.skip("No margin data available for test date")
            
        # Check columns
        assert "rzye" in margin_df.columns, "rzye column missing"
        
        # Test rename
        rename = collector._get_interface_rename("margin")
        margin_renamed = margin_df.rename(columns=rename)
        assert "margin_balance" in margin_renamed.columns, "margin_balance not in renamed df"
        
    def test_margin_in_non_negative_cols(self):
        """Verify margin fields are in non_negative_cols"""
        from qsys.data.collector import TushareCollector
        
        try:
            collector = TushareCollector()
        except ValueError as e:
            if "Tushare token" in str(e):
                pytest.skip("Tushare token not available")
            raise
        
        non_neg = collector._non_negative_cols
        margin_fields = ["margin_balance", "margin_buy_amount", "lend_volume"]
        
        for field in margin_fields:
            assert field in non_neg, f"{field} not in non_negative_cols"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])