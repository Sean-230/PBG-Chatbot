from api.upload_csv import process_csv, read_csv, embed_rows, upsert_rows
from dotenv import load_dotenv
import os
import google.genai
from pinecone import Pinecone

load_dotenv("api/.env")

# Re-implement process_csv just for 0-1050 to be extremely safe
filepath = "/Users/seantandjaja/Documents/PERIZINAN PBG - Transaksi2.csv"
rows = read_csv(filepath)
rows_to_process = rows[0:1050] # EXPLICITLY ONLY 0 to 1050

genai_client = google.genai.Client(api_key=os.environ["GEMINI_API_KEY"])
pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
index = pc.Index(os.environ["PINECONE_INDEX_NAME"])

# Using the patched embed_rows which upserts per batch
print(f"Starting batch of {len(rows_to_process)} rows...")
embed_rows(genai_client, index, rows_to_process)
print("Finished!")
