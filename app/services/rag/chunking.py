def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """
    Splits text into semantic chunks of approximately `chunk_size` words 
    with `overlap` words between consecutive chunks.
    """
    if not text:
        return []
        
    words = text.split()
    chunks = []
    
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        
        if end == len(words):
            break
            
        start += (chunk_size - overlap)
        
    return chunks
