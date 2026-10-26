"""Legacy module name for the versioned API router."""
import sys
from api import routes as implementation
sys.modules[__name__] = implementation
