from setuptools import setup


setup(
    name="agentdm",
    version="0.1.0",
    description="Agent Direct Message CLI",
    packages=["agentdm"],
    entry_points={
        "console_scripts": [
            "agentdm=agentdm.cli:main",
        ]
    },
)
