
import datetime
from alphafeed import AlphaFeed

af = AlphaFeed(api_key="sk_717e3bbb0212491cb4ac92932343abf8")

# 获取日K线
# df = af.klines.get("000001.SH", to_dataframe=True)
# print(df)

# 批量获取实时行情
# df = af.quotes.get(symbols=["000001.SH", "000002.SH", "301217.SZ"], to_dataframe=True)
# print(df['trade_time'])
# print(type(df['trade_time']))
df = af.quotes.get(universes=["CN_Index"], to_dataframe=True)
# print(df.columns.tolist())
print(df)
# df 是你的行情DataFrame
# for idx, row in df.iterrows():
#     # row 是 Series，用列名取值
#     code = row["symbol"]
#     name = row["ext.name"]
#     price = row["last_price"]
#     pct = row["ext.change_pct"]
#     print(f"{code} {name} 现价:{price} 涨跌幅:{pct}%")
#     if code == '000001.SH' or code == '000016.SH' or code == '000002.SH':
#         print(f"{code} {name} 现价:{price} 涨跌幅:{pct}%")

# start = int(datetime.datetime(2026, 6, 1).timestamp() * 1000)
# end = int(datetime.datetime(2026, 6, 20).timestamp() * 1000)
# df = af.klines.get("000001.SH", period="1d", start_time=start, end_time=end, to_dataframe=True)
# print(f"2026-06-01 ~ 2026-06-20 共 {len(df)} 个交易日")
# print(df[["trade_date", "open", "close", "volume"]].tail(5).to_string(index=False))