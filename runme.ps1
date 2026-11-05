.\.venv\Scripts\activate
uv pip install -r requirements.txt     # installs the neo4j driver
uv pip install -e .
sr neo4j-start      # first run downloads Neo4j (~130 MB) into var\neo4j, then starts it
sr neo4j-check      # should print counts and "constraint/index created"
sr import-v1        # loads the 50 reviewed drawings (~20 s, no LLM cost)
sr serve            # UI at http://127.0.0.1:8077