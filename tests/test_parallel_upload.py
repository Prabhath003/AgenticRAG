#!/usr/bin/env python3
"""
Test script for parallel document upload performance

This script tests the parallelization improvements by uploading documents
to multiple entities concurrently.
"""

import os
import sys
import time
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add src to path
# sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.core.entity_scoped_rag import get_entity_rag_manager
from src.log_creator import get_file_logger

logger = get_file_logger()


def create_test_document(filename: str, content: str) -> str:
    """Create a temporary test document"""
    temp_dir = tempfile.mkdtemp()
    file_path = os.path.join(temp_dir, filename)

    with open(file_path, 'w') as f:
        f.write(content)

    return file_path


def upload_document(entity_id: str, file_path: str, doc_num: int):
    """Upload a single document and measure time"""
    manager = get_entity_rag_manager()

    start_time = time.time()
    result = manager.add_document(entity_id, file_path)
    elapsed = time.time() - start_time

    return {
        'entity_id': entity_id,
        'doc_num': doc_num,
        'elapsed': elapsed,
        'success': result is not None,
        'doc_id': result.get('doc_id') if result else None
    }


def test_sequential_upload(num_entities: int, docs_per_entity: int):
    """Test sequential document upload"""
    logger.info(f"\n{'='*60}")
    logger.info(f"SEQUENTIAL UPLOAD TEST")
    logger.info(f"Entities: {num_entities}, Docs per entity: {docs_per_entity}")
    logger.info(f"{'='*60}")

    # Create test documents
    test_files = []
    for i in range(docs_per_entity):
        content = f"Test document {i}\n" + ("Lorem ipsum dolor sit amet. " * 100)
        file_path = create_test_document(f"test_doc_{i}.txt", content)
        test_files.append(file_path)

    start_time = time.time()
    results = []

    # Sequential upload
    for entity_num in range(num_entities):
        entity_id = f"entity_{entity_num}"
        for doc_num, file_path in enumerate(test_files):
            result = upload_document(entity_id, file_path, doc_num)
            results.append(result)
            logger.info(f"✓ {entity_id}/doc_{doc_num}: {result['elapsed']:.2f}s")

    total_time = time.time() - start_time
    avg_time = total_time / len(results)

    logger.info(f"\n📊 Sequential Results:")
    logger.info(f"  Total time: {total_time:.2f}s")
    logger.info(f"  Avg per doc: {avg_time:.2f}s")
    logger.info(f"  Docs uploaded: {sum(1 for r in results if r['success'])}/{len(results)}")

    # Cleanup
    for file_path in test_files:
        os.remove(file_path)

    return total_time, results


def test_parallel_upload(num_entities: int, docs_per_entity: int, max_workers: int = None):
    """Test parallel document upload"""
    if max_workers is None:
        max_workers = min(32, (os.cpu_count() or 4) * 2)

    logger.info(f"\n{'='*60}")
    logger.info(f"PARALLEL UPLOAD TEST")
    logger.info(f"Entities: {num_entities}, Docs per entity: {docs_per_entity}")
    logger.info(f"Max workers: {max_workers}")
    logger.info(f"{'='*60}")

    # Create test documents
    test_files = []
    for i in range(docs_per_entity):
        content = f"Test document {i}\n" + ("Lorem ipsum dolor sit amet. " * 100)
        file_path = create_test_document(f"test_doc_{i}.txt", content)
        test_files.append(file_path)

    start_time = time.time()
    results = []

    # Parallel upload
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []

        for entity_num in range(num_entities):
            entity_id = f"entity_{entity_num}"
            for doc_num, file_path in enumerate(test_files):
                future = executor.submit(upload_document, entity_id, file_path, doc_num)
                futures.append((entity_id, doc_num, future))

        # Wait for completion
        for entity_id, doc_num, future in futures:
            try:
                result = future.result(timeout=60)
                results.append(result)
                logger.info(f"✓ {entity_id}/doc_{doc_num}: {result['elapsed']:.2f}s")
            except Exception as e:
                logger.error(f"✗ {entity_id}/doc_{doc_num}: {e}")
                results.append({
                    'entity_id': entity_id,
                    'doc_num': doc_num,
                    'success': False,
                    'elapsed': 0
                })

    total_time = time.time() - start_time
    avg_time = total_time / len(results) if results else 0

    logger.info(f"\n📊 Parallel Results:")
    logger.info(f"  Total time: {total_time:.2f}s")
    logger.info(f"  Avg per doc: {avg_time:.2f}s")
    logger.info(f"  Docs uploaded: {sum(1 for r in results if r['success'])}/{len(results)}")

    # Cleanup
    for file_path in test_files:
        os.remove(file_path)

    return total_time, results


def main():
    """Run parallel upload tests"""
    print("\n🚀 PARALLEL DOCUMENT UPLOAD PERFORMANCE TEST\n")

    # Test configuration
    NUM_ENTITIES = 3
    DOCS_PER_ENTITY = 2
    MAX_WORKERS = 8

    # Test 1: Sequential upload (baseline)
    seq_time, seq_results = test_sequential_upload(NUM_ENTITIES, DOCS_PER_ENTITY)

    # Test 2: Parallel upload
    par_time, par_results = test_parallel_upload(NUM_ENTITIES, DOCS_PER_ENTITY, MAX_WORKERS)

    # Compare results
    speedup = seq_time / par_time if par_time > 0 else 0

    print(f"\n{'='*60}")
    print(f"📈 PERFORMANCE COMPARISON")
    print(f"{'='*60}")
    print(f"Sequential time: {seq_time:.2f}s")
    print(f"Parallel time:   {par_time:.2f}s")
    print(f"Speedup:         {speedup:.2f}x")
    print(f"Efficiency:      {(speedup / MAX_WORKERS * 100):.1f}%")
    print(f"{'='*60}\n")

    if speedup > 1.5:
        print("✅ Parallel upload is significantly faster!")
    elif speedup > 1.0:
        print("⚠️  Parallel upload is faster, but not by much")
    else:
        print("❌ Parallel upload is not faster - check for locking issues")

    return speedup


if __name__ == "__main__":
    try:
        speedup = main()
        sys.exit(0 if speedup > 1.0 else 1)
    except Exception as e:
        logger.error(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
