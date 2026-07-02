from backend.utils.diagram_reader import read_diagram
import os
import uuid
from qdrant_client.models import PointStruct
from backend.utils.drawio_reader import read_drawio
from backend.utils.pdf_diagram_reader import read_pdf_diagram
from backend.utils.pdf_smart_reader import read_pdf_smart
from backend.utils.dependencies import EMBEDDING_MODEL, QDRANT_CLIENT, COLLECTION_NAME
from backend.utils.file_readers import read_pdf, read_docx, read_excel
from backend.utils.text_chunker import split_text
from datetime import datetime
from backend.services.metadata_generator import generate_summary
from backend.services.keyword_generator import generate_keywords
from backend.services.document_profile_service import add_profile
from backend.services.activity_service import log_activity
from backend.services.graph_service import create_document, create_keyword, create_relationship
from backend.services.profile_embedding_service import build_profile_embeddings

def index_file(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            text = read_pdf_smart(path)
        elif ext == ".docx":
            text = read_docx(path)
        elif ext in [".xlsx", ".xls"]:
            text = read_excel(path)
        elif ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp"]:
            text = read_diagram(path)
        elif ext == ".drawio":
            from backend.utils.drawio_reader import read_drawio, parse_drawio_structured
            from backend.services.graph_service import ingest_diagram
            text = read_drawio(path)
            structured_data = parse_drawio_structured(path)
            if structured_data and structured_data.get("nodes"):
                ingest_diagram(os.path.basename(path), structured_data["nodes"], structured_data["edges"])
        elif ext == ".txt":
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        elif ext == ".pbix":
            from backend.utils.pbix_reader import read_pbix_and_extract_csv
            text = read_pbix_and_extract_csv(path)
        else:
            return

        if not text.strip():
            return
            
        file_name = os.path.basename(path)
        display_name = os.path.splitext(file_name)[0].replace("_", " ").replace("-", " ")
        
        print(f"\nReading {file_name}")
        print("Generating Summary...")
        summary = generate_summary(text)
        
        print("Generating Keywords...")
        keywords = generate_keywords(text)
        
        print("\nSummary:")
        print(summary)
        print("\nKeywords:")
        print(keywords)
        
        print("Chunking...")
        chunks = split_text(text)
        
        total_chunks = len(chunks)
        file_type = os.path.splitext(path)[1].replace(".", "")
        source_folder = os.path.basename(os.path.dirname(path))
        
        profile = {
            "file_name": file_name,
            "display_name": display_name,
            "summary": summary,
            "keywords": keywords,
            "folder": source_folder,
            "file_type": file_type,
            "total_chunks": total_chunks,
            "indexed_at": datetime.now().isoformat()
        }
        add_profile(profile)
        
        create_document(profile)
        for keyword in keywords:
            create_keyword(keyword)
            create_relationship(file_name, keyword)
        
        points = []
        
        for chunk_number, chunk in enumerate(chunks, start=1):
            if not chunk.strip():
                continue
                
            # ENRICH CHUNK WITH CONTEXT
            # We prepend the filename to the chunk so the embedding model knows 
            # which file this text belongs to. This fixes the issue where a chunk 
            # has "CGPA: 8.5" but doesn't explicitly mention "Ruthvik".
            chunk_text_with_context = f"File Name: {file_name}\n\n{chunk}"
            
            embedding = EMBEDDING_MODEL.encode(chunk_text_with_context).tolist()
            
            points.append(
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=embedding,
                    payload={
                        "file_name": file_name,
                        "display_name": display_name,
                        "summary": summary,
                        "keywords": keywords,
                        "file_type": file_type,
                        "chunk_number": chunk_number,
                        "total_chunks": total_chunks,
                        "source_folder": source_folder,
                        "indexed_at": datetime.now().isoformat(),
                        "content": chunk_text_with_context
                    }
                )
            )

        print("Embedding...")
        QDRANT_CLIENT.upsert(
            collection_name=COLLECTION_NAME,
            points=points
        )
        print(f"Uploading...\nDone. INDEXED SUCCESSFULLY: {path} ({len(chunks)} chunks)")
        
        build_profile_embeddings()
        
        log_activity("Qdrant Updated", file_name)
        log_activity("Document Indexed", file_name)

    except Exception as e:
        print(f"\nFAILED TO INDEX: {path}")
        print(e)
        log_activity("Error occurred", os.path.basename(path) if 'path' in locals() else "unknown", "Error")

def index_enterprise_document(doc):
    try:
        if not doc.content.strip():
            return
            
        file_name = doc.metadata.get("file_name", doc.title)
        display_name = doc.title
        
        print(f"\nReading {file_name} from {doc.source}")
        print("Generating Summary...")
        summary = generate_summary(doc.content)
        
        print("Generating Keywords...")
        keywords = generate_keywords(doc.content)
        
        print("Chunking...")
        chunks = split_text(doc.content)
        total_chunks = len(chunks)
        
        file_type = doc.metadata.get("file_type", "txt")
        source_folder = doc.metadata.get("folder", doc.source)
        
        profile = {
            "file_name": file_name,
            "display_name": display_name,
            "summary": summary,
            "keywords": keywords,
            "folder": source_folder,
            "file_type": file_type,
            "total_chunks": total_chunks,
            "indexed_at": datetime.now().isoformat()
        }
        add_profile(profile)
        
        create_document(profile)
        for keyword in keywords:
            create_keyword(keyword)
            create_relationship(file_name, keyword)
        
        points = []
        for chunk_number, chunk in enumerate(chunks, start=1):
            if not chunk.strip():
                continue
                
            chunk_text_with_context = f"File Name: {file_name}\n\n{chunk}"
            embedding = EMBEDDING_MODEL.encode(chunk_text_with_context).tolist()
            
            points.append(
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=embedding,
                    payload={
                        "file_name": file_name,
                        "display_name": display_name,
                        "summary": summary,
                        "keywords": keywords,
                        "file_type": file_type,
                        "chunk_number": chunk_number,
                        "total_chunks": total_chunks,
                        "source_folder": source_folder,
                        "indexed_at": datetime.now().isoformat(),
                        "content": chunk_text_with_context
                    }
                )
            )

        print("Embedding...")
        QDRANT_CLIENT.upsert(
            collection_name=COLLECTION_NAME,
            points=points
        )
        print(f"Uploading...\nDone. INDEXED SUCCESSFULLY: {file_name} ({len(chunks)} chunks)")
        
        build_profile_embeddings()
        
        log_activity("Qdrant Updated", file_name)
        log_activity(f"{doc.source.title()} Ingested", file_name)
        log_activity("Document Indexed", file_name)

    except Exception as e:
        print(f"\nFAILED TO INDEX: {doc.title}")
        print(e)
        log_activity("Error occurred", doc.title if doc else "unknown", "Error")

def delete_enterprise_document(file_name):
    from backend.services.graph_service import delete_document
    from backend.services.document_profile_service import delete_profile
    from backend.services.profile_embedding_service import build_profile_embeddings
    from backend.utils.dependencies import QDRANT_CLIENT, COLLECTION_NAME
    from qdrant_client.http import models
    from backend.services.activity_service import log_activity
    
    try:
        print(f"\nDeleting {file_name} from all systems...")
        delete_document(file_name)
        delete_profile(file_name)
        build_profile_embeddings()
        
        QDRANT_CLIENT.delete(
            collection_name=COLLECTION_NAME,
            points_selector=models.Filter(
                must=[models.FieldCondition(key="file_name", match=models.MatchValue(value=file_name))]
            )
        )
        log_activity("Document Deleted", file_name)
        print(f"DELETED SUCCESSFULLY: {file_name}")
    except Exception as e:
        print(f"\nFAILED TO DELETE: {file_name}")
        print(e)
