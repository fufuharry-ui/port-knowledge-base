import os
import pytest
from datetime import datetime
from unittest.mock import patch
from scripts.logger import ActivityLogger

@pytest.fixture
def temp_wiki_dir(tmp_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    return wiki_dir

def test_logger_creates_file_if_not_exists(temp_wiki_dir):
    logger = ActivityLogger(base_dir=temp_wiki_dir)
    log_file = temp_wiki_dir / "log.md"
    
    assert not log_file.exists()
    logger.log("ingest", "test_file.txt", "Pages created: wiki/doc_001.yaml")
    assert log_file.exists()

@patch('scripts.logger.datetime')
def test_logger_format(mock_datetime, temp_wiki_dir):
    # Mock datetime to a fixed value
    mock_datetime.now.return_value = datetime(2026, 4, 5, 10, 0, 0)
    
    logger = ActivityLogger(base_dir=temp_wiki_dir)
    logger.log(
        action="ingest",
        target="岸桥远控技术方案",
        details="Pages created: wiki/doc_20260405_001.summary.yaml\nPages updated: meta/ontology/global_ontology.yaml"
    )
    
    log_file = temp_wiki_dir / "log.md"
    with open(log_file, 'r', encoding='utf-8') as f:
        content = f.read()
        
    expected_entry = (
        "## [2026-04-05 10:00:00] ingest | 岸桥远控技术方案\n"
        "Pages created: wiki/doc_20260405_001.summary.yaml\n"
        "Pages updated: meta/ontology/global_ontology.yaml\n\n"
    )
    
    assert expected_entry in content

def test_logger_append_only(temp_wiki_dir):
    logger = ActivityLogger(base_dir=temp_wiki_dir)
    
    logger.log("ingest", "file1.txt", "Details 1")
    logger.log("search", "query1", "Details 2")
    
    log_file = temp_wiki_dir / "log.md"
    with open(log_file, 'r', encoding='utf-8') as f:
        content = f.read()
        
    assert "file1.txt" in content
    assert "query1" in content
    assert content.find("file1.txt") < content.find("query1")
