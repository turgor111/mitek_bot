import json
import os
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio

# MongoDB setup
MONGO_URI = 'mongodb://127.0.0.1:27017/'
client = AsyncIOMotorClient(MONGO_URI)
db = client['telegram_bot']
collection_1 = db['phrases_list_1']
collection_2 = db['phrases_list_2']

# File paths for the phrase lists
PHRASE_FILE_1 = 'phrases_list_1.json'
PHRASE_FILE_2 = 'phrases_list_2.json'

async def load_phrases(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r') as file:
            return json.load(file)
    return []

async def populate_collection(collection, phrases):
    if phrases:
        await collection.delete_many({})  # Clear existing data
        await collection.insert_many([{'phrase': phrase} for phrase in phrases])
        print(f"Inserted {len(phrases)} phrases into {collection.name}")

async def main():
    phrases_1 = await load_phrases(PHRASE_FILE_1)
    phrases_2 = await load_phrases(PHRASE_FILE_2)
    
    await populate_collection(collection_1, phrases_1)
    await populate_collection(collection_2, phrases_2['phrases'])

if __name__ == '__main__':
    asyncio.run(main())
