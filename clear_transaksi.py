import os
from pinecone import Pinecone
from dotenv import load_dotenv

load_dotenv("api/.env")
pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
index = pc.Index("pbg-knowledge")

try:
    print("Mencoba menghapus dengan filter metadata...")
    # "filename" -> "PERIZINAN PBG - Transaksi2.csv"
    index.delete(filter={"source": "PERIZINAN PBG - Transaksi2.csv"})
    print("Berhasil menghapus dengan filter!")
except Exception as e:
    print(f"Gagal: {e}")
