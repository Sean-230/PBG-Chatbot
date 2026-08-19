with open("api/upload_csv.py", "r") as f:
    lines = f.readlines()

out = []
in_embed = False
for line in lines:
    if "def embed_rows(" in line:
        line = line.replace("def embed_rows(genai_client, rows", "def embed_rows(genai_client, index, rows")
    if 'r["embedding"] = resp.embeddings[i].values' in line:
        out.append(line)
        out.append('                    batch_embedded.append(r)\n')
        continue
    if "embedded.append(r)" in line:
        # Before the loop over batch, we need batch_embedded = []
        pass
    if "for attempt in range(5):" in line:
        out.append("        batch_embedded = []\n")
    if "break" in line and "batch_embedded.append(r)" in out[-1]:
        out.append('                upsert_rows(index, batch_embedded)\n')
        
    out.append(line)

# Let's use sed instead. It's much safer.
