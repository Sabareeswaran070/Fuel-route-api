import csv, math, time, requests, os
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
import polyline as pl

# Config / constants
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_ROUTE_URL = "http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=polyline&steps=false"
# CSV path relative to project root (manage.py location)
FUEL_CSV = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'fuel_prices.csv')
# normalize
FUEL_CSV = os.path.abspath(FUEL_CSV)
VEHICLE_MPG = 10.0
MAX_RANGE_MILES = 500.0
FUEL_CAPACITY_GALLONS = MAX_RANGE_MILES / VEHICLE_MPG
SEARCH_RADIUS_MILES = 20.0  # radius to look for a cheap station around a refill point

# Geocoding cache for common US cities (avoids rate limiting on Nominatim)
GEOCODE_CACHE = {
    'new york, ny': (40.7128, -74.0060),
    'los angeles, ca': (34.0522, -118.2437),
    'chicago, il': (41.8781, -87.6298),
    'houston, tx': (29.7604, -95.3698),
    'phoenix, az': (33.4484, -112.0740),
    'philadelphia, pa': (39.9526, -75.1652),
    'san antonio, tx': (29.4241, -98.4936),
    'san diego, ca': (32.7157, -117.1611),
    'dallas, tx': (32.7767, -96.7970),
    'san jose, ca': (37.3382, -121.8863),
    'austin, tx': (30.2672, -97.7431),
    'jacksonville, fl': (30.3322, -81.6557),
    'san francisco, ca': (37.7749, -122.4194),
    'columbus, oh': (39.9612, -82.9988),
    'indianapolis, in': (39.7684, -86.1581),
    'fort worth, tx': (32.7555, -97.3308),
    'charlotte, nc': (35.2271, -80.8431),
    'seattle, wa': (47.6062, -122.3321),
    'denver, co': (39.7392, -104.9903),
    'washington, dc': (38.9072, -77.0369),
    'boston, ma': (42.3601, -71.0589),
    'el paso, tx': (31.7619, -106.4850),
    'nashville, tn': (36.1627, -86.7816),
    'detroit, mi': (42.3314, -83.0458),
    'oklahoma city, ok': (35.4676, -97.5164),
    'portland, or': (45.5152, -122.6784),
    'las vegas, nv': (36.1699, -115.1398),
    'memphis, tn': (35.1495, -90.0490),
    'louisville, ky': (38.2527, -85.7585),
    'baltimore, md': (39.2904, -76.6122),
    'milwaukee, wi': (43.0389, -87.9065),
    'albuquerque, nm': (35.0844, -106.6504),
    'tucson, az': (32.2226, -110.9747),
    'fresno, ca': (36.7378, -119.7871),
    'sacramento, ca': (38.5816, -121.4944),
    'atlanta, ga': (33.7490, -84.3880),
    'kansas city, mo': (39.0997, -94.5786),
    'miami, fl': (25.7617, -80.1918),
    'cleveland, oh': (41.4993, -81.6944),
    'raleigh, nc': (35.7796, -78.6382),
    'omaha, ne': (41.2565, -95.9345),
    'minneapolis, mn': (44.9778, -93.2650),
    'tampa, fl': (27.9506, -82.4572),
    'tulsa, ok': (36.1540, -95.9928),
    'arlington, tx': (32.7357, -97.1081),
    'new orleans, la': (29.9511, -90.0715),
}

def geocode(address):
    """Geocode an address using cache first, then Nominatim (OpenStreetMap)"""
    # Try cache first
    normalized_address = address.lower().strip()
    if normalized_address in GEOCODE_CACHE:
        return GEOCODE_CACHE[normalized_address]
    
    # Fall back to Nominatim
    headers = {
        "User-Agent": "RouteFuelOptimizer/1.0 (Django API for fuel planning; contact@example.com)",
        "Referer": "http://localhost:8000"
    }
    params = {"q": address, "format": "json", "limit": 1, "countrycodes": "us"}
    try:
        r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        return float(data[0]['lat']), float(data[0]['lon'])
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 403:
            raise Exception("Nominatim geocoding blocked. Please use cached city names or wait and try again.")
        raise


def haversine_miles(a, b):
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    R = 3958.8  # miles
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(h))

def load_stations(csv_path):
    stations = []
    if not os.path.exists(csv_path):
        raise FileNotFoundError(csv_path)
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                lat = float(r['lat'])
                lon = float(r['lon'])
                price = float(r['price'])
                name = r.get('name','')
                stations.append({'lat':lat,'lon':lon,'price':price,'name':name})
            except Exception:
                continue
    return stations

def find_nearby_cheapest(stations, lat, lon, radius_miles=SEARCH_RADIUS_MILES):
    candidates = []
    for s in stations:
        d = haversine_miles((lat,lon),(s['lat'],s['lon']))
        if d <= radius_miles:
            candidates.append((s['price'], d, s))
    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1]))
        return candidates[0][2], candidates[0][1]
    # fallback nearest
    nearest = min(stations, key=lambda s: haversine_miles((lat,lon),(s['lat'],s['lon'])))
    return nearest, haversine_miles((lat,lon),(nearest['lat'], nearest['lon']))

class RouteFuelView(APIView):
    """POST expects JSON: { "start_address": "...", "end_address": "..." }
    Returns: route distance, polyline geojson, planned fuel stops, estimated cost.
    """
    def post(self, request):
        payload = request.data
        start = payload.get('start_address')
        end = payload.get('end_address')
        if not start or not end:
            return Response({'error':'start_address and end_address required'}, status=status.HTTP_400_BAD_REQUEST)

        # 1) geocode
        try:
            start_latlon = geocode(start)
            time.sleep(1.0)
            end_latlon = geocode(end)
        except Exception as e:
            return Response({'error':'geocoding failure', 'details':str(e)}, status=500)
        if not start_latlon or not end_latlon:
            return Response({'error':'could not geocode one of the addresses'}, status=400)

        # 2) OSRM route call
        osrm_url = OSRM_ROUTE_URL.format(lat1=start_latlon[0], lon1=start_latlon[1],
                                        lat2=end_latlon[0], lon2=end_latlon[1])
        try:
            r = requests.get(osrm_url, timeout=15)
            r.raise_for_status()
            j = r.json()
            if 'routes' not in j or not j['routes']:
                return Response({'error':'no route found'}, status=400)
            route = j['routes'][0]
            distance_meters = route['distance']
            distance_miles = distance_meters / 1609.344
            poly = pl.decode(route['geometry'])  # list of (lat,lon)
        except Exception as e:
            return Response({'error':'routing failure', 'details':str(e)}, status=500)

        # 3) load stations
        try:
            stations = load_stations(FUEL_CSV)
        except FileNotFoundError:
            return Response({'error':f'Fuel CSV not found at {FUEL_CSV}. Provide CSV with fields lat,lon,price,name'}, status=500)

        # 4) traverse route and plan stops
        stops = []
        remaining_range = MAX_RANGE_MILES
        consumed_miles = 0.0
        i = 0
        # We'll step along the polyline points
        while i < len(poly)-1:
            seg_start = poly[i]
            seg_end = poly[i+1]
            seg_dist = haversine_miles(seg_start, seg_end)
            if seg_dist <= remaining_range:
                remaining_range -= seg_dist
                consumed_miles += seg_dist
                i += 1
                continue
            # need fuel before we can traverse seg_end
            # choose current point as refill locus approximation
            proj_lat, proj_lon = seg_start
            station, dist_to_station = find_nearby_cheapest(stations, proj_lat, proj_lon)
            # simulate filling to full
            gallons_needed = max(0.0, FUEL_CAPACITY_GALLONS - (remaining_range / VEHICLE_MPG))
            if gallons_needed < 0.001:
                gallons_needed = 0.0
            stops.append({
                'lat': station['lat'],
                'lon': station['lon'],
                'name': station.get('name',''),
                'price_per_gallon': station['price'],
                'distance_from_route_point_miles': round(dist_to_station,3),
                'mile_marker': round(consumed_miles,2),
                'gallons_bought': round(gallons_needed,3),
                'cost': round(gallons_needed * station['price'], 2)
            })
            # update totals and reset remaining range (assume full tank)
            remaining_range = MAX_RANGE_MILES
            # do not advance i to attempt same segment again
        total_gallons_consumed = distance_miles / VEHICLE_MPG
        total_cost = sum(s['cost'] for s in stops)
        resp = {
            'start': {'address': start, 'latlon': start_latlon},
            'end': {'address': end, 'latlon': end_latlon},
            'distance_miles': round(distance_miles,2),
            'estimated_total_gallons_consumed': round(total_gallons_consumed,3),
            'stops': stops,
            'total_cost_estimate': round(total_cost,2),
            'polyline_geojson': {'type':'LineString','coordinates':[[p[1],p[0]] for p in poly]}
        }
        mapquest_key = getattr(settings, 'MAPQUEST_KEY', None)
        if mapquest_key:
            coords_for_shape = "|".join(f"{p[0]},{p[1]}" for p in poly[::max(1,len(poly)//50)])
            static_url = f"https://www.mapquestapi.com/staticmap/v5/map?key={mapquest_key}&size=1024,600&shape={coords_for_shape}"
            resp['static_map_url'] = static_url
        return Response(resp)
