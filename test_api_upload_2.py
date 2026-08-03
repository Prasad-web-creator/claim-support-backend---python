import asyncio
import httpx

async def test_upload():
    async with httpx.AsyncClient() as client:
        login_res = await client.post("http://localhost:8000/api/auth/login", json={"phone": "1234567890", "otp": "1234"})
        token = login_res.json()["token"]
        
        headers = {"Authorization": f"Bearer {token}"}
        file_content = b"%PDF-1.4\nSome dummy content here"
        
        # OMIT data (documentType) to mimic the mobile app
        files = {"file": ("test.pdf", file_content, "application/pdf")}
        
        upload_res = await client.post(
            "http://localhost:8000/api/upload", 
            headers=headers, 
            files=files
        )
        print("Upload Response Code:", upload_res.status_code)
        print("Upload Response Body:", upload_res.text)

asyncio.run(test_upload())
