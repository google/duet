# Copyright 2021 The Duet Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import pathlib

from setuptools import setup

# This reads the __version__ variable from duet/_version.py
__version__ = ""
exec(pathlib.Path("duet/_version.py").read_text())

name = "duet"

description = "A simple future-based async library for python."

# README file as long_description.
long_description = pathlib.Path("README.md").read_text()

# If DUET_PRE_RELEASE_VERSION is set then we update the version to this value.
# It is assumed that it ends with one of `.devN`, `.aN`, `.bN`, `.rcN` and hence
# it will be a pre-release version on PyPi. See
# https://packaging.python.org/guides/distributing-packages-using-setuptools/#pre-release-versioning
# for more details.
if "DUET_PRE_RELEASE_VERSION" in os.environ:
    __version__ = os.environ["DUET_PRE_RELEASE_VERSION"]
    long_description = "\n\n".join(
        [
            "This is a development version of Duet and may be unstable.",
            "For the latest stable release see https://pypi.org/project/duet-async/.",
            long_description,
        ]
    )

# Sanity check
assert __version__, "Version string cannot be empty"

# Read requirements
requirements = [line.strip() for line in open("requirements.txt").readlines()]
dev_requirements = [line.strip() for line in open("dev/requirements.txt").readlines()]

setup(
    name=name,
    version=__version__,
    url="http://github.com/google/duet",
    author="The Duet Authors",
    author_email="maffoo@google.com",
    python_requires=">=3.6.0",
    install_requires=requirements,
    extras_require={
        "dev_env": dev_requirements,
    },
    license="Apache 2",
    description=description,
    long_description=long_description,
    long_description_content_type='text/markdown',
    packages=["duet"],
    package_data={
        "duet": ["py.typed"],
    },
)
