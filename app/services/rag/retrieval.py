from typing import List, Dict, Any
from google.genai import types
from app.models.document_chunk import DocumentChunk
from app.services.rag.embedding import generate_embedding
from app.services.llm.ai_client import get_ai_client
from app.core.config import get_settings
from app.core.logging import logger

async def retrieve_relevant_chunks(
    question: str, 
    user_id: str, 
    policy_id: str, 
    prescription_id: str, 
    top_k: int = 5
) -> List[Dict[str, Any]]:
    """
    Embeds the user's question, searches MongoDB via Vector Search, 
    and returns the top_k most relevant chunks restricted to the user's specific policy and prescription.
    """
    logger.info(f"Generating embedding for question: {question}")
    question_embedding = await generate_embedding(question)
    
    # MongoDB Atlas Vector Search Aggregation Pipeline
    pipeline = [
        {
            "$vectorSearch": {
                "index": "vector_index",  # Must match the name of the Atlas Search Index created
                "path": "embedding",
                "queryVector": question_embedding,
                "numCandidates": 50,
                "limit": top_k,
                "filter": {
                    "$and": [
                        {"user_id": {"$eq": user_id}},
                        {"document_id": {"$in": [policy_id, prescription_id]}}
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "text": 1,
                "document_type": 1,
                "page_number": 1,
                "score": {"$meta": "vectorSearchScore"}
            }
        }
    ]
    
    logger.info(f"Executing Vector Search with policy_id={policy_id} and prescription_id={prescription_id}")
    try:
        results = await DocumentChunk.aggregate(pipeline).to_list()
        logger.info(f"Vector search returned {len(results)} chunks.")
        return results
    except Exception as e:
        logger.exception("Vector Search aggregation failed")
        # Note: This will fail locally if not using Atlas or if index 'vector_index' is missing.
        # Ensure that the index is created in Atlas!
        raise ValueError(f"Vector search failed. Ensure the search index 'vector_index' exists. Details: {e}")

async def ask_rag_assistant(
    question: str, 
    user_id: str, 
    policy_id: str, 
    prescription_id: str
) -> Dict[str, Any]:
    """
    End-to-end RAG question answering.
    """
    chunks = await retrieve_relevant_chunks(question, user_id, policy_id, prescription_id)
    
    if not chunks:
        return {
            "answer": "No relevant information was found in the selected documents.",
            "sources": []
        }
        
    # Construct context from chunks
    context_text = ""
    for i, chunk in enumerate(chunks):
        doc_type = chunk.get('document_type', 'unknown')
        page = chunk.get('page_number') or 'unknown'
        text = chunk.get('text', '')
        context_text += f"\n--- Source {i+1} ({doc_type} | Page {page}) ---\n{text}\n"

    system_prompt = (
        "You are an intelligent AI Assistant for an Insurance Claim Support application.\n"
        "Your role is to accurately answer the user's question using ONLY the provided document context.\n"
        "The context consists of chunks extracted from the user's insurance policy and prescription.\n"
        "\n"
        "RULES:\n"
        "1. Answer ONLY using the retrieved document context below.\n"
        "2. If the answer is not present in the retrieved context, reply EXACTLY with: 'The uploaded documents do not contain enough information to answer this question.'\n"
        "3. NEVER invent coverage details.\n"
        "4. NEVER hallucinate information.\n"
        "5. Be clear, concise, and helpful.\n"
        "\n"
        f"CONTEXT:\n{context_text}"
    )

    settings = get_settings()
    model_name = settings.AI_MODEL
    client = get_ai_client()
    
    logger.info(f"Sending prompt to Gemini ({model_name})...")
    response = await client.aio.models.generate_content(
        model=model_name,
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.1
        )
    )
    
    answer = response.text
    
    # Format sources for the response
    sources = []
    for chunk in chunks:
        sources.append({
            "documentType": chunk.get('document_type'),
            "pageNumber": chunk.get('page_number') or 1
        })
        
    return {
        "answer": answer,
        "sources": sources
    }
