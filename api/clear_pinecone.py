import os
import logging
from pathlib import Path

from dotenv import load_dotenv
from pinecone import Pinecone

# Load environment variables
_HERE = Path(__file__).resolve().parent
load_dotenv(dotenv_path=_HERE / ".env", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PINECONE_INDEX_NAME = os.environ.get("PINECONE_INDEX_NAME", "pbg-knowledge")
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "")

def clear_all():
    if not PINECONE_API_KEY:
        logger.error("PINECONE_API_KEY tidak ditemukan di file .env")
        return
    
    logger.info("Menghubungkan ke Pinecone...")
    pc = Pinecone(api_key=PINECONE_API_KEY)
    
    try:
        index = pc.Index(PINECONE_INDEX_NAME)
        logger.info(f"Menghapus semua data (vectors) di index '{PINECONE_INDEX_NAME}'...")
        
        # Perintah ini akan menghapus semua vektor di index Pinecone
        index.delete(delete_all=True)
        
        logger.info("✅ Semua data di Pinecone berhasil dihapus! Database sekarang kosong.")
    except Exception as e:
        logger.error(f"Gagal menghapus data Pinecone: {e}")

if __name__ == "__main__":
    konfirmasi = input("Apakah Anda yakin ingin menghapus SEMUA DATA di Pinecone? (y/n): ")
    if konfirmasi.lower() == 'y':
        clear_all()
    else:
        print("Dibatalkan.")
