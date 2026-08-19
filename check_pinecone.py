from pinecone import Pinecone
import os
from dotenv import load_dotenv

load_dotenv("api/.env")
pc = Pinecone(api_key=os.environ.get("PINECONE_API_KEY"))
index = pc.Index("pbg-knowledge")
stats = index.describe_index_stats()
print("TOTAL VECTORS IN PINECONE:", stats.total_vector_count)
