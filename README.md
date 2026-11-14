# TruckGPS truck-restriction database

Monthly build of truck restrictions (height, width, length, weight, axle load,
"no trucks" roads) from OpenStreetMap for the regions in `build/regions.txt`.

The GitHub Action downloads the Geofabrik extracts, keeps only restriction data,
and publishes `restrictions.db.gz` + `manifest.json` as the release **latest**.
The TruckGPS app downloads it through the TruckGPS Cloudflare relay.

Run it manually: **Actions → Build truck restriction database → Run workflow**.

Data © OpenStreetMap contributors, available under the Open Database License (ODbL 1.0).
