#!/usr/bin/env python

"""Generate SasView parameter resources

This script populates:
    - ./template.json (from ./template.json.template)
    - ./algorithms/sasview.json (from ./algorithms/sasview.json.template)
    - ./resources/inputParams*.json
"""

import os
import sys
import json
import re

from sasmodels.sasview_model import load_standard_models
from sasmodels.weights import MODELS as POLYDISPERSITY_MODELS
import bumps.fitters

# import sas
sys.path.append(os.environ.get("ODF_SASVIEW_SRC") + "/src")


DEFAULT_PARAMS_RESOURCE = "inputParamsSphere"
DEFAULT_SF_PARAMS_RESOURCE = None
DEFAULT_OPTIONS_RESOURCE = None

DEFAULT_OPTIONS_RESOURCE = "inputOptions"


def diff(list1, list2):
    # Returns difference between two lists
    return list(set(list1) - set(list2))


def index(lst, key, value):
    # Find index of item matching i[key] == value in a list of dicts
    for i, dic in enumerate(lst):
        if dic[key] == value:
            return i
    return None


def model_name_to_title(name):
    # Convenience method for converting model names to human readable titles
    exceptions = {
        "hardsphere": "Hard Sphere",
        "stickyhardsphere": "Sticky Hard Sphere",
        "squarewell": "Square Well",
        "hayter_msa": "Hayter MSA",
        "bcc_paracrystal": "BCC Paracrystal",
        "fcc_paracrystal": "FCC Paracrystal",
        "sc_paracrystal": "SC Paracrystal",
        "be_polyelectrolyte": "BE Polyelectrolyte",
        "rpa": "RPA",
        "dab": "DAB",
        "shape-independent": "Shape-independent",
    }

    if name in exceptions:
        return exceptions[name]
    elif "shape:" in name:
        return ": ".join([i.capitalize() for i in name.split(":")])
    else:
        return " ".join([i.capitalize() for i in name.split("_")])


def fit_method_name_to_title(name):
    # Convenience method - convert fit method name to human readable title
    exceptions = {
        "LevenbergMarquardtFit": "Levenberg-Marquardt Fit",
    }

    if name in exceptions:
        return exceptions[name]
    else:
        # See https://stackoverflow.com/a/9283563/6412264
        return re.sub(r"((?<=[a-z])[A-Z]|(?<!\A)[A-Z](?=[a-z]))", r" \1", name)


def get_fit_methods():
    class_list = dir(bumps.fitters)

    def is_fitter(name):
        if name != "FitBase" and name != "FitDriver":
            return "Fit" in name
        return False

    fitter_list = list(filter(is_fitter, class_list))

    # getattr(bumps.fitters, fitter).id

    return [
        {"name": name, "title": fit_method_name_to_title(name)}
        for name in fitter_list
    ]


def get_options_resource():
    # Get list of available fit methods and their titles
    methods = get_fit_methods()

    # Generate model enum options
    models = []
    sf_models = []

    for model in load_standard_models():
        if model.is_structure_factor:
            sf_models.append(
                {
                    "title": model_name_to_title(model.name),
                    "name": model.name,
                }
            )
        else:
            # Enable/disable structure factor selection
            # Based on sasview logic in perspectives/fitting/basepage.py:2026
            if (
                not hasattr(model, "is_form_factor")
                or not model.is_form_factor
            ):
                sf = False
            else:
                sf = True

            models.append(
                {
                    "title": model_name_to_title(
                        model.name
                    ),  # Human readable name
                    "name": model.name,  # Model key
                    "category": model_name_to_title(
                        model.category
                    ),  # List category
                    # Enable/disable structure factor selection
                    "structureFactor": sf,
                }
            )

    # Add "None" option for structure factor selection
    sf_models.append(
        {
            "title": "None",
            "name": "",
        }
    )

    # Define schema for options resource
    fields = [
        {
            "name": "method",
            "title": "Fit method",
            "description": "The optimisation method to use for fitting",
            "type": "object",
            "objectFields": [
                {
                    "name": "title",
                    "type": "string",
                },
                {
                    "name": "name",
                    "type": "string",
                },
            ],
            "constraints": {
                "enum": methods,
            },
        },
        {
            "name": "model",
            "title": "Fit model",
            "description": "Model to use for fitting",
            "type": "object",
            "objectFields": [
                {
                    "name": "title",
                    "type": "string",
                },
                {
                    "name": "name",
                    "type": "string",
                },
                {
                    "name": "category",
                    "type": "string",
                    "description": "Model category",
                },
                {
                    "name": "structureFactor",
                    "type": "string",
                    "description": (
                        "Whether structure factor fitting is "
                        "available for this model"
                    ),
                },
            ],
            "constraints": {
                "enum": models,
            },
        },
        {
            "name": "structureFactor",
            "title": "Structure factor model",
            "description": "Structure factor model to fit",
            "type": "object",
            "objectFields": [
                {
                    "name": "title",
                    "type": "string",
                },
                {
                    "name": "name",
                    "type": "string",
                },
            ],
            "constraints": {
                "enum": sf_models,
            },
        },
    ]

    # Default selections
    # TODO: This will go away, default should be in parameter space selection
    data = {
        "method": methods[0],
        "model": next(i for i in models if i["name"] == "sphere"),
        "structureFactor": next(
            i for i in sf_models if i["name"] == "hayter_msa"
        ),
    }

    # Build resource
    resource = {
        "name": DEFAULT_OPTIONS_RESOURCE,
        "profile": "opendatafit-options",
        "data": data,
        "schema": {
            "fields": fields,
        },
    }

    return resource


def base_model_to_param_resource(model):
    # Build resource containing a list of available params for a given sasmodel
    params = diff(model.getParamList(), model.getDispParamList())

    # Data resource
    data = {}
    keys = []

    for param in params:
        # No orientation params in 1D
        if param not in model.orientation_params:
            # Populate in case defaults are empty
            # [0]: Units, [1]: Min, [2]: Max
            if param not in model.details:
                model.details[param] = ["", None, None]

            value = model.getParam(param)  # Default value
            lower = model.details[param][1]
            upper = model.details[param][2]
            unit = model.details[param][0]

            # If not fittable still acts as fixed variable but should not be
            # able to be selected for fitting
            # fittable = param not in model.non_fittable

            # Default all parameters to fixed/non-variable
            vary = False

            keys.append(
                {
                    "name": param,
                    "unit": unit,
                    "fields": [
                        {
                            "name": "vary",
                            "title": "Vary",
                            "type": "boolean",
                        },
                        {
                            "name": "value",
                            "title": "Value",
                            "type": "number",
                        },
                        {
                            "name": "lowerBound",
                            "group": "constraints",
                            "title": "Lower bound",
                            "type": "number",
                        },
                        {
                            "name": "upperBound",
                            "group": "constraints",
                            "title": "Upper bound",
                            "type": "number",
                        },
                        # {
                        #    "name": "fittable",
                        #    "title": "Fittable",
                        #    "type": "boolean",
                        # },
                    ],
                }
            )

            data[param] = {
                "vary": vary,
                "value": value,
                "lowerBound": lower,
                "upperBound": upper,
                # "fittable": fittable,
            }

    if model.is_structure_factor:
        name_prefix = "inputSFParams"
    else:
        name_prefix = "inputParams"

    resource = {
        "name": name_prefix + model_name_to_title(model.name).replace(" ", ""),
        "metadata": {
            "model": {
                "name": model.name,
                "title": model_name_to_title(model.name),
            },
        },
        "data": data,
        "schema": {
            "keys": keys,
        },
        "profile": "parameter-data-resource",
    }

    return resource


def model_to_param_resource(model, polydispersity=False):
    # Build base params resource
    resource = base_model_to_param_resource(model)

    # Return only base model params if polydispersity disabled
    if not polydispersity:
        return resource

    # Polydispersity type options enum
    # Default: gaussian
    pd_types = [name for name in POLYDISPERSITY_MODELS.keys()]

    # Add polydispersity group definition
    resource["schema"]["groups"] = [
        {
            "name": "polydispersity",
            "title": "Polydispersity",
        }
    ]

    # Dispersion parameters
    for param in model.dispersion.keys():
        # No orientation params in 1D
        # Exclude magnetic parameters
        if (
            param not in model.orientation_params
            and param not in model.magnetic_params
        ):
            # To reference individual param in bumps:
            # param + key (width/npts/nsigma/type)
            disp = model.dispersion[param]

            width = disp["width"]
            width_key = param + ".width"
            width_lower = model.details[width_key][1]
            width_upper = model.details[width_key][2]
            width_unit = model.details[width_key][0]

            npts = disp["npts"]
            nsigma = disp["nsigmas"]
            pd_type = disp["type"]

            vary = False  # default

            # Set param names
            param_pd = param + "_pd"
            param_pd_n = param + "_pd_n"
            param_pd_nsigma = param + "_pd_nsigma"
            param_pd_type = param + "_pd_type"
            # Append polydispersity parameter metadata to parent key fields
            resource["schema"]["keys"].extend(
                [
                    {
                        "name": param_pd,
                        "title": "Width",
                        "description": "Polydispersity width of parameter:"
                        + param,
                        "unit": width_unit,
                        "groups": ["polydispersity"],
                        "fields": [
                            {
                                "name": "vary",
                                "title": "Vary",
                                "type": "boolean",
                            },
                            {
                                "name": "value",
                                "title": "Value",
                                "type": "integer",
                                "constraints": {
                                    "min": 0,
                                },
                            },
                            {
                                "name": "lowerBound",
                                "title": "Lower bound",
                                "type": "number",
                            },
                            {
                                "name": "upperBound",
                                "title": "Upper bound",
                                "type": "number",
                            },
                            # {
                            #    "name": "fittable",
                            #    "title": "Fittable",
                            #    "type": "boolean",
                            # },
                        ],
                    },
                    {
                        "name": param_pd_n,
                        "title": "N points",
                        "description": "Number of points",
                        "groups": ["polydispersity"],
                        "fields": [
                            {
                                "name": "value",
                                "title": "Value",
                                "type": "integer",
                                "constraints": {
                                    "min": 0,
                                },
                            },
                        ],
                    },
                    {
                        "name": param_pd_nsigma,
                        "title": "N sigmas",
                        "groups": ["polydispersity"],
                        "fields": [
                            {
                                "name": "value",
                                "title": "Value",
                                "type": "number",
                            },
                        ],
                    },
                    {
                        "name": param_pd_type,
                        "title": "Type",
                        "description": "Polydispersity type",
                        "groups": ["polydispersity"],
                        "fields": [
                            {
                                "name": "value",
                                "title": "Value",
                                "type": "string",
                                "constraints": {
                                    "enum": pd_types,
                                },
                            },
                        ],
                    },
                ]
            )

            # Add polydispersity parameter values
            resource["data"].update(
                {
                    param_pd: {
                        "value": width,
                        "lowerBound": width_lower,
                        "upperBound": width_upper,
                        # "fittable": True,
                        "vary": vary,
                    },
                    param_pd_n: {
                        "value": npts,
                    },
                    param_pd_nsigma: {
                        "value": nsigma,
                    },
                    param_pd_type: {
                        "value": pd_type,
                    },
                }
            )

            # Add polydispersity parameters to relatedKeys on parent param if
            # found

            # Get index of parent param in keys list
            ind = index(resource["schema"]["keys"], "name", param)

            if ind is not None:
                resource["schema"]["keys"][ind].update(
                    {
                        "relatedKeys": [
                            param_pd,
                            param_pd_n,
                            param_pd_nsigma,
                            param_pd_type,
                        ]
                    }
                )

    return resource


if __name__ == "__main__":
    # Load all available sasmodels (as SasviewModel classes)

    # SasviewModel - apparently a sasview wrapper for kernel model used in
    # bumps. These are *somehow* converted into bumps models through a
    # convoluted network of classes incl. SasFitness, FitProblem, BumpsFit
    # See:
    # sascalc/fit/BumpsFitting.py
    # perspectives/fitting/fitting.py
    # perspectives/fitting/fitproblem.py
    # perspectives/fitting/fitstate.py

    param_resources = []
    sf_param_resources = []

    # Populate resources directory
    for model in load_standard_models():
        resource = model_to_param_resource(
            model(),
            polydispersity=True,
        )

        path = "./resources/" + resource["name"] + ".json"

        if model().is_structure_factor:
            sf_param_resources.append(resource["name"])
        else:
            param_resources.append(resource["name"])

        with open(path, "w") as f:
            # Replace infinite bounds with nulls
            f.write(
                json.dumps(resource, indent=2)
                .replace(": Infinity", ": null")
                .replace(": -Infinity", ": null")
            )

    # Populate sasview algorithm json
    with open("./algorithms/sasview.json.template", "r") as f:
        algorithm = json.load(f)

    # Set default resources
    algorithm["inputs"][0]["resource"] = {
        "params": DEFAULT_PARAMS_RESOURCE,
        "sfParams": DEFAULT_SF_PARAMS_RESOURCE,
        "options": DEFAULT_OPTIONS_RESOURCE,
    }

    # Populate enums
    algorithm["inputs"][0]["resources"]["params"] = param_resources
    algorithm["inputs"][0]["resources"]["sfParams"] = sf_param_resources
    algorithm["inputs"][0]["resources"]["options"] = []

    with open("./algorithms/sasview.json", "w") as f:
        json.dump(algorithm, f, indent=2)

    # Populate template json
    with open("./template.json.template", "r") as f:
        template = json.load(f)

    template["resources"].extend(
        [{"name": name} for name in param_resources + sf_param_resources]
    )

    with open("./template.json", "w") as f:
        json.dump(template, f, indent=2)

    # Populate options resource
    with open("./resources/" + DEFAULT_OPTIONS_RESOURCE + ".json", "w") as f:
        json.dump(get_options_resource(), f, indent=2)

    print("Done!")
