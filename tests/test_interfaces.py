import sys
import unittest
from pathlib import Path

# Add src to python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from interfaces import BaseConnector, BaseParser, BaseVectorStore

class TestInterfaces(unittest.TestCase):
    def test_base_connector_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            BaseConnector()

    def test_base_parser_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            BaseParser()

    def test_base_vector_store_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            BaseVectorStore()

    def test_concrete_connector_instantiation(self):
        class ConcreteConnector(BaseConnector):
            def connect(self):
                pass
            def fetch_documents(self):
                yield {}

        connector = ConcreteConnector()
        self.assertIsInstance(connector, BaseConnector)
        self.assertEqual(list(connector.fetch_documents()), [{}])

    def test_concrete_parser_instantiation(self):
        class ConcreteParser(BaseParser):
            def parse(self, doc_data):
                return [{"chunk_id": "1"}]

        parser = ConcreteParser()
        self.assertIsInstance(parser, BaseParser)
        self.assertEqual(parser.parse({}), [{"chunk_id": "1"}])

    def test_concrete_vector_store_instantiation(self):
        class ConcreteVectorStore(BaseVectorStore):
            def upsert_chunks(self, chunks):
                pass
            def search(self, query, user_permissions, k=3):
                return []

        store = ConcreteVectorStore()
        self.assertIsInstance(store, BaseVectorStore)
        self.assertEqual(store.search("test", ["admin"]), [])

if __name__ == "__main__":
    unittest.main()
