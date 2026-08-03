import asyncio
import http

async def test_upload():
    # Login to get a token
    async with http.AsyncClient() as client:
        # First, login
        login_res = await client.post("http://localhost:8000/api/auth/login", json={"phone": "1234567890", "otp": "1234"})
        if login_res.status_code != 200:
            print("Login failed:", login_res.text)
            return
        
        token = login_res.json()["token"]
        print("Got token")
        
        # Now try uploading
        headers = {"Authorization": f"Bearer {token}"}
        
        # Create a dummy PDF file content
        file_content = b"%PDF-1.4\nSome dummy content here"
        
        # HTTP multipart upload requires files={"file": ("test.pdf", file_content, "application/pdf")}
        files = {"file": ("test.pdf", file_content, "application/pdf")}
        data = {"documentType": "prescription"}
        
        upload_res = await client.post(
            "http://localhost:8000/api/upload/", 
            headers=headers, 
            files=files, 
            data=data
        )
        print("Upload Response Code:", upload_res.status_code)
        print("Upload Response Body:", upload_res.text)

asyncio.run(test_upload())
