import shutil
import os

if os.path.exists("my_scraper_api"):
    if os.path.exists("api"):
        shutil.rmtree("api")
    shutil.copytree("my_scraper_api", "api")
    print("Copied my_scraper_api to api")
    
    if os.path.exists("api/main.py"):
        os.rename("api/main.py", "api/index.py")
        print("Renamed main.py to index.py")
        
    shutil.rmtree("my_scraper_api")
    print("Removed my_scraper_api")
else:
    print("my_scraper_api not found")

