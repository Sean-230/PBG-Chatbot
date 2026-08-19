import json
import os
from dotenv import load_dotenv

load_dotenv("api/.env")

from api.tools import check_brangkas
print(check_brangkas("6680"))
