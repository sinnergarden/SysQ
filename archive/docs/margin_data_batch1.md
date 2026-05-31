"""
Margin Financing (两融) Data Documentation

## First Batch Fields

### Margin Interface Fields (from Tushare `margin` API)
| Field | Tushare Name | Description | Type |
|-------|-------------|-------------|------|
| margin_balance | rzye | 融资余额 (Financing Balance) | float (万元) |
| margin_buy_amount | rzmre | 融资买入额 (Financing Buy Amount) | float (万元) |
| margin_repay_amount | rzche | 融资偿还额 (Financing Repay Amount) | float (万元) |
| margin_total_balance | rzrqye | 融资融券余额 (Total Margin Balance) | float (万元) |
| lend_volume | rqyl | 融券余量 (Lending Volume) | float (股) |
| lend_sell_volume | rqmcl | 融券卖出量 (Lending Sell Volume) | float (股) |
| lend_repay_volume | rqrchl | 融券偿还量 (Lending Repay Volume) | float (股) |

## Data Characteristics

### Missingness Behavior
- **Coverage**: Margin data is available for most A-share stocks but NOT all
  - ST stocks may not have margin trading
  - Some small cap stocks may not be eligible
  - New listings (within ~3 months) may not have data
  
### Lag Behavior
- **T+1**: Data is published on T+1 (next trading day around 16:00)
- **Weekends/Holidays**: No data on non-trading days
- **Year-end**: High volatility around year-end due to margin settlement

### Data Unit Conversion
- Tushare returns:
  - 融资余额/买入/偿还 (rzye, rzmre, rzche): in 万元 (10,000 RMB)
  - 融券余量 (rqyl): in 股 (shares)
  - 融券卖出/偿还 (rqmcl, rqrchl): in 股 (shares)

## Expected Missingness in Production
- First batch fields are expected to have ~5-15% missing on any given day
- Missingness is higher for:
  - Small cap stocks (< 10亿市值)
  - ST/*ST stocks
  - Newly listed stocks
  - Stocks with suspended margin trading

## Future Batches (Not Implemented)
- 融资融券明细 (margin_detail) - per-transaction level
- 担保品明细 (margin_sure) - collateral details
- 合约展期 (margin_extend) - contract rollover data
"""