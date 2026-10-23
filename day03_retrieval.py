"""第 3 天的本地部分：文档切分 → 中文向量 → Chroma → 检索。"""

from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import chromadb
from chromadb.config import Settings
from chromadb.errors import NotFoundError
from fastembed import TextEmbedding


ROOT = Path(__file__).parent
KNOWLEDGE_DIR = ROOT / "knowledge"
DATABASE_DIR = ROOT / "artifacts/chroma_day03"
MODEL_CACHE = ROOT / "artifacts/embedding_cache"
COLLECTION_NAME = "day03_product_docs_v1"
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
CHUNK_SIZE = 240
CHUNK_OVERLAP = 40


def read_chunks() -> tuple[list[dict], str, int]:
    """保留文件名和分段编号；这是按字符切分的教学实现。"""
    paths = sorted(KNOWLEDGE_DIR.glob("*.md"))
    if not paths:
        raise ValueError("knowledge/ 中没有 Markdown 文档。")
    fingerprint = sha256(f"{EMBEDDING_MODEL}:{CHUNK_SIZE}:{CHUNK_OVERLAP}".encode())
    chunks = []
    for path in paths:
        content = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
        if not content:
            raise ValueError(f"文档为空：{path.name}")
        fingerprint.update(path.name.encode() + b"\0" + content.encode() + b"\0")
        title = content.splitlines()[0].lstrip("# ")
        start = 0
        index = 1
        while start < len(content):
            end = min(start + CHUNK_SIZE, len(content))
            chunks.append({
                "id": f"{path.name}#chunk-{index}",
                "source": path.name,
                "chunk_index": index,
                "text": f"{title}\n{content[start:end]}",
            })
            if end == len(content):
                break
            start = end - CHUNK_OVERLAP
            index += 1
    return chunks, fingerprint.hexdigest(), len(paths)


@lru_cache(maxsize=2)
def embedding_model(allow_download: bool = False) -> TextEmbedding:
    """进程内只加载一次；缓存模型后可在本地生成向量。"""
    try:
        return TextEmbedding(
            model_name=EMBEDDING_MODEL,
            cache_dir=str(MODEL_CACHE),
            providers=["CPUExecutionProvider"],
            threads=2,
            local_files_only=not allow_download,
        )
    except Exception as error:
        raise RuntimeError(
            "中文向量模型加载失败；请联网运行 --index 下载或修复本地缓存。"
            f"错误类型：{type(error).__name__}。"
        ) from error


def database():
    return chromadb.PersistentClient(
        path=str(DATABASE_DIR), settings=Settings(anonymized_telemetry=False)
    )


def build_index() -> None:
    chunks, fingerprint, document_count = read_chunks()
    print(f"读取 {document_count} 篇演示文档，切成 {len(chunks)} 个片段。", flush=True)
    print(f"本地生成中文向量：{EMBEDDING_MODEL}", flush=True)
    vectors = [vector.tolist() for vector in embedding_model(allow_download=True).embed(
        [chunk["text"] for chunk in chunks], batch_size=32
    )]
    client = database()
    # 先成功生成全部向量，再替换本课专用集合；不清空其他集合。
    try:
        client.delete_collection(COLLECTION_NAME)
    except NotFoundError:
        pass
    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}},
        metadata={"fingerprint": fingerprint, "embedding_model": EMBEDDING_MODEL},
    )
    collection.add(
        ids=[chunk["id"] for chunk in chunks],
        documents=[chunk["text"] for chunk in chunks],
        embeddings=vectors,
        metadatas=[{"source": chunk["source"], "chunk_index": chunk["chunk_index"]}
                   for chunk in chunks],
    )
    print(f"索引完成：{collection.count()} 个片段，每个向量 {len(vectors[0])} 维。")
    print(f"索引位置：{DATABASE_DIR}")


def search_docs(question: str, top_k: int = 3, min_similarity: float = 0.35) -> list[dict]:
    """只做本地检索，不调用 DeepSeek；相似度不是回答正确率。"""
    if not question.strip():
        raise ValueError("问题不能为空。")
    if not 1 <= top_k <= 10 or not -1 <= min_similarity <= 1:
        raise ValueError("top_k 必须为 1–10，相似度阈值必须为 -1–1。")
    try:
        collection = database().get_collection(COLLECTION_NAME, embedding_function=None)
    except NotFoundError as error:
        raise ValueError("尚未建立知识库，请先运行 uv run day03_rag.py --index。") from error
    _, fingerprint, _ = read_chunks()
    if (collection.metadata or {}).get("fingerprint") != fingerprint:
        raise ValueError("知识文档或分块配置已改变，请重新运行 --index 更新索引。")
    count = collection.count()
    if count == 0:
        raise ValueError("索引没有片段，请重新运行 --index。")
    vector = next(embedding_model().embed([question])).tolist()
    result = collection.query(
        query_embeddings=[vector], n_results=min(top_k, count),
        include=["documents", "metadatas", "distances"],
    )
    hits = []
    for chunk_id, text, metadata, distance in zip(
        result["ids"][0], result["documents"][0],
        result["metadatas"][0], result["distances"][0], strict=True
    ):
        similarity = 1 - distance  # 集合使用 cosine 距离。
        if similarity >= min_similarity:
            hits.append({"id": chunk_id, "source": metadata["source"],
                         "text": text, "similarity": round(similarity, 4)})
    return hits


def show_hits(hits: list[dict]) -> None:
    if not hits:
        print("没有找到达到相似度阈值的文档片段。")
    for index, hit in enumerate(hits, 1):
        print(f"\n[S{index}] {hit['id']} | 余弦相似度 {hit['similarity']:.4f}")
        print(hit["text"])
