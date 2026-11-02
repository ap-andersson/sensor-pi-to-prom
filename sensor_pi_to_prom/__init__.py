import os

# Set at image build time (see Dockerfile / GitHub workflow).
__version__ = os.getenv("APP_VERSION", "dev")
