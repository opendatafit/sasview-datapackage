#!/usr/bin/env python

import json
import base64


# Load datapackage.json
with open("datapackage.json") as f:
    datapackage = json.load(f)


# Load input file and pre-populate data resource
# with open("input.xml", "rb") as f:
with open("input.xml", "rb") as f:
    input_data = base64.b64encode(f.read()).decode("utf-8")

# Get input data resource to be modified
data_resource = next(
    i for i in datapackage["resources"] if i["name"] == "inputData"
)

# Populate data
data_resource["title"] = "input.xml"
data_resource["data"] = input_data

# Write input.json for testing local execution
with open("input.json", "w") as f:
    json.dump(datapackage, f, indent=2)
