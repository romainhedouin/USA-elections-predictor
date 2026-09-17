# Superseded Selenium scraper

This is how the project read NBC News before it switched to their JSON API
(see `../nbc_api.py`). It drives headless Chrome over each state's results
page, clicks the "show all counties" toggle — the rendered HTML only carries
a handful of county rows until you do — dumps each row's outerHTML, and
parses it with BeautifulSoup.

It works, and it is kept because it depends on nothing but the public page:
if NBC ever retires or locks down `/firecracker/api/v2/`, this is the
fallback. But it is strictly worse for everyday use:

- ~4 minutes for 51 states, against ~10 seconds for the API
- needs Chrome and a few hundred MB of RAM, which is the difference between
  a small container and a big one
- gets no county FIPS, so its output can only be joined to a map by name —
  which is wrong in ten states (see the README's county section)

It also writes the older CSV schema (`County` instead of `Area`, and no
`FIPS` / `Geography` columns), so `map.html` will not render its output
without a shim.

```
python legacy/list_states.py --race senate --nbc-cycle 2024
python legacy/generate_raw_data.py --race senate --nbc-cycle 2024
```
