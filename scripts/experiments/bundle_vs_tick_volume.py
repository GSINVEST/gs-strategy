"""比對 bundle 的 TX 成交量與 tick 的 volume_bs（驗證 volume_bs 是否為雙邊合計、bundle 是否僅日盤）。"""
import pandas as pd, pyarrow.parquet as pq, glob
b=pd.read_parquet(glob.glob("/home/kevin/.zipline/data/tquant_future/2026-10-29*/future_raw.parquet")[0])
b=b[b.symbol.str.match(r"^TX\d{6}$")].copy(); b["expiry"]=b.symbol.str[2:]; b["date"]=pd.to_datetime(b.date).dt.date
print(sorted(b.symbol.unique())[:3], b.date.min(), b.date.max())
root="/home/kevin/gs-scraper/QUANTDATA/silver/ticks/taifex_futures"
rows=[]
for y in (2023,2024,2025):
    t=pq.read_table(f"{root}/year={y}/part-{y}.parquet",columns=["trade_date","product","expiry","trade_time","volume_bs"],filters=[("product","=","TX")],partitioning=None).to_pandas()
    t=t[t.expiry.str.fullmatch(r"\d{6}")]
    t["day"]=(t.trade_time>="084500")&(t.trade_time<="134500")
    g=t.groupby(["trade_date","expiry"]).apply(lambda x: pd.Series(dict(all=x.volume_bs.sum(),day=x.volume_bs[x.day].sum())),include_groups=False).reset_index()
    rows.append(g)
g=pd.concat(rows).rename(columns={"trade_date":"date"})
m=b.merge(g,on=["date","expiry"])
m=m[m.volume>5000]
m["r_all"]=m.volume/m["all"]; m["r_day"]=m.volume/m["day"]
print(len(m)); print(m[["r_all","r_day"]].describe().round(3).to_string())
print(m.groupby(pd.to_datetime(m.date).dt.year)[["r_all","r_day"]].median().round(3))
