#!/usr/bin/env python

import json
import base64

# Load datapackage template
with open("template.json") as f:
    dp = json.load(f)


# Populate template items from individual json subdirectories
def populate_items(dp, key):
    items = []
    for i in dp[key]:
        with open(key + "/" + i["name"] + ".json") as f:
            # Load individual item json definition
            item = json.load(f)

            # If algorithm, populate code field in base64
            if key == "algorithms" and "code" in item:
                with open(key + "/" + i["name"] + ".py", "rb") as c:
                    item["code"] = base64.b64encode(c.read()).decode("utf-8")

            items.append(item)
    dp[key] = items


for key in ["algorithms", "resources", "views", "displays"]:
    populate_items(dp, key)

# Write complete datapackage to single file
with open("datapackage.json", "w") as f:
    json.dump(dp, f, indent=2)

print("Datapackage written to datapackage.json")
