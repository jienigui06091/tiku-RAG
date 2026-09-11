import unittest

from app.services.documents import chunk_document, extract_text_from_bytes


class DocumentChunkingTests(unittest.TestCase):
    def test_chunks_keep_section_and_page_metadata(self):
        first_page = "# Introduction\n" + ("First paragraph. " * 80)
        second_page = "# Details\n" + ("Second paragraph. " * 80)

        chunks = chunk_document(f"{first_page}\f{second_page}", chunk_size=180, chunk_overlap=30)

        self.assertGreater(len(chunks), 2)
        self.assertEqual(chunks[0].source_page_start, 1)
        self.assertEqual(chunks[-1].source_page_start, 2)
        self.assertEqual(chunks[0].chapter, "# Introduction")
        self.assertEqual(chunks[-1].chapter, "# Details")
        self.assertTrue(all(len(chunk.content) <= 200 for chunk in chunks))

    def test_text_extraction_accepts_uploaded_bytes(self):
        text, page_count = extract_text_from_bytes("content".encode(), ".txt")

        self.assertEqual(text, "content")
        self.assertIsNone(page_count)

    def test_interface_model_splits_on_request_method_and_path(self):
        text = "\n".join(
            [
                "POST /api/v1/orders",
                "Create an order with the supplied items.",
                "Request body: items and customer id.",
                "GET /api/v1/orders/{id}",
                "Return an order by id.",
            ]
        )

        chunks = chunk_document(text, chunk_size=500, chunk_overlap=0, chunk_model="interface")

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].chapter, "POST /api/v1/orders")
        self.assertEqual(chunks[1].chapter, "GET /api/v1/orders/{id}")
        self.assertTrue(chunks[0].content.startswith("POST /api/v1/orders"))

    def test_fixed_model_does_not_add_section_context(self):
        text = "# Create order\n" + ("Order details. " * 20)

        chunks = chunk_document(
            text,
            chunk_size=120,
            chunk_overlap=0,
            chunk_model="fixed",
            retain_context=False,
        )

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.chapter is None for chunk in chunks))

    def test_delimiter_model_splits_at_custom_delimiter(self):
        delimiter = "========================"
        text = f"Create order endpoint\nPOST /orders\n{delimiter}\nGet order endpoint\nGET /orders/{{id}}"

        chunks = chunk_document(
            text,
            chunk_size=500,
            chunk_overlap=0,
            chunk_model="delimiter",
            custom_delimiter=delimiter,
        )

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].content, "Create order endpoint\nPOST /orders")
        self.assertEqual(chunks[1].content, "Get order endpoint\nGET /orders/{id}")
        self.assertTrue(all(delimiter not in chunk.content for chunk in chunks))

    def test_delimiter_model_requires_a_delimiter(self):
        with self.assertRaisesRegex(ValueError, "custom_delimiter"):
            chunk_document("content", 500, 0, chunk_model="delimiter")


if __name__ == "__main__":
    unittest.main()
