import os, math
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static")
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")
UA = os.getenv("SCRAPER_USER_AGENT", "MarketplaceRadar/2.0")
NOMINATIM_URL = "https://nominatim.openstreetmap.org"

def reverse_geocode(lat, lon):
    r = requests.get(f"{NOMINATIM_URL}/reverse", params={"lat":lat,"lon":lon,"format":"json","zoom":18}, headers={"User-Agent":UA}, timeout=10)
    r.raise_for_status()
    return r.json().get("address",{}).get("postcode","")

def ebay_token():
    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET: return None
    r=requests.post("https://api.ebay.com/identity/v1/oauth2/token", auth=(EBAY_CLIENT_ID,EBAY_CLIENT_SECRET), data={"grant_type":"client_credentials","scope":"https://api.ebay.com/oauth/api_scope"}, headers={"Content-Type":"application/x-www-form-urlencoded"}, timeout=15)
    r.raise_for_status(); return r.json()["access_token"]

def ebay_search(keyword, postcode, radius, limit=50):
    token=ebay_token()
    if not token or not postcode: return [], "eBay requires EBAY_CLIENT_ID/EBAY_CLIENT_SECRET and a UK postcode."
    params={"q":keyword,"limit":min(limit,200),"sort":"newlyListed","filter":f"pickupCountry:GB,pickupPostalCode:{postcode},pickupRadius:{radius},pickupRadiusUnit:km"}
    r=requests.get("https://api.ebay.com/buy/browse/v1/item_summary/search", params=params, headers={"Authorization":f"Bearer {token}","X-EBAY-C-MARKETPLACE-ID":"EBAY_GB"}, timeout=20); r.raise_for_status()
    out=[]
    for x in r.json().get("itemSummaries",[]):
        loc=x.get("itemLocation") or {}; out.append({"source":"eBay","title":x.get("title",""),"price":(x.get("price") or {}).get("value"),"currency":"GBP","url":x.get("itemWebUrl") or x.get("itemHref",""),"image":(x.get("image") or {}).get("imageUrl"),"location":", ".join(v for v in [loc.get("city"),loc.get("postalCode")] if v),"posted":x.get("itemOriginDate")})
    return out,None

def gumtree_search(keyword, lat, lon, radius, limit=50):
    try:
        r=requests.get("https://www.gumtree.com/search?"+urlencode({"q":keyword,"search_location":f"{lat},{lon}","radius":radius}),headers={"User-Agent":UA},timeout=20); r.raise_for_status()
    except Exception as e: return [],f"Gumtree request failed: {e}"
    soup=BeautifulSoup(r.text,"html.parser"); out=[]; seen=set()
    for a in soup.select("a[href*='/p/']"):
        href=a.get("href"); title=a.get_text(" ",strip=True)
        if not href or not title or len(title)<3 or href in seen: continue
        seen.add(href); out.append({"source":"Gumtree","title":title,"price":None,"currency":"GBP","url":href if href.startswith("http") else "https://www.gumtree.com"+href,"image":None,"location":"","posted":None})
        if len(out)>=limit: break
    return out,None

def facebook_search(keyword, lat, lon, radius, limit=50):
    return [], "Facebook Marketplace is enabled, but a general public Marketplace search API is not available to this app. An approved Facebook integration is required to populate Facebook results."

@app.get("/")
def index(): return send_from_directory("static","index.html")

@app.get("/api/search")
def search():
    keywords=[x.strip() for x in request.args.get("keywords","").split(",") if x.strip()]; radius=float(request.args.get("radius","20")); lat=float(request.args["lat"]); lon=float(request.args["lon"]); postcode=request.args.get("postcode","").strip()
    if not postcode:
        try: postcode=reverse_geocode(lat,lon)
        except Exception: postcode=""
    items=[]; errors=[]
    for kw in keywords:
        for fn,args in [(ebay_search,(kw,postcode,radius)),(gumtree_search,(kw,lat,lon,radius)),(facebook_search,(kw,lat,lon,radius))]:
            vals,err=fn(*args); items += vals
            if err: errors.append(f"{fn.__name__.replace('_search','').title()} / {kw}: {err}")
    dedup={x["url"] or (x["source"],x["title"]):x for x in items}; items=list(dedup.values())
    def ts(x):
        try: return datetime.fromisoformat((x.get("posted") or "").replace("Z","+00:00")).timestamp()
        except: return 0
    items.sort(key=ts,reverse=True)
    return jsonify({"items":items,"errors":errors,"keywords":keywords,"radius_km":radius,"postcode_used":postcode,"sources":["eBay","Gumtree","Facebook Marketplace"],"updated_at":datetime.now(timezone.utc).isoformat()})

if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.getenv("PORT","8000")),debug=True)
