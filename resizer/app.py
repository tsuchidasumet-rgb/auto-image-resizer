import json
import boto3
import io
from PIL import Image, ImageDraw, ImageFont

s3 = boto3.client('s3')

def lambda_handler(event, context):
    try:
        # 1. รับข้อมูลจาก Event ของ S3
        bucket = event['Records'][0]['s3']['bucket']['name']
        key = event['Records'][0]['s3']['object']['key']
        
        # 2. ดึงรูปและ Metadata (เพื่อเอาชื่อ User)
        response = s3.get_object(Bucket=bucket, Key=key)
        metadata = response.get('Metadata', {})
        user_name = metadata.get('username', 'Unknown Seller')
        
        image_content = response['Body'].read()
        
        # 3. เริ่มจัดการรูปภาพ
        with Image.open(io.BytesIO(image_content)) as img:
            img = img.convert("RGBA")
            width, height = img.size
            
            # สร้าง Layer สำหรับลายน้ำ
            txt_layer = Image.new("RGBA", img.size, (255, 255, 255, 0))
            draw = ImageDraw.Draw(txt_layer)
            
            # ปรับขนาดฟอนต์ตามความกว้างรูป (ประมาณ 4%)
            font_size = int(width * 0.04)
            try:
                # แก้ชื่อไฟล์ฟอนต์ให้ตรงกับที่คุณโหลดมาใส่ในโฟลเดอร์นะครับ
                font = ImageFont.truetype("Montserrat-Bold.ttf", font_size)
            except:
                font = ImageFont.load_default()

            text = f"Seller: {user_name}"
            
            # คำนวณตำแหน่งมุมขวาล่าง
            margin = int(width * 0.05)
            left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
            text_width = right - left
            text_height = bottom - top
            position = (width - text_width - margin, height - text_height - margin)

            # วาดลายน้ำ (สีขาวโปร่งแสง)
            draw.text(position, text, fill=(255, 255, 255, 160), font=font)
            
            # รวมรูปและเซฟเป็น JPEG
            final_img = Image.alpha_composite(img, txt_layer).convert("RGB")
            buffer = io.BytesIO()
            final_img.save(buffer, format="JPEG", quality=85)
            buffer.seek(0)

            # 4. ส่งรูปที่ทำเสร็จแล้วกลับไปที่โฟลเดอร์ processed/
            output_key = key.replace('uploads/', 'processed/')
            s3.put_object(
                Bucket=bucket,
                Key=output_key,
                Body=buffer,
                ContentType='image/jpeg'
            )
            
        return {"statusCode": 200, "body": "Success"}

    except Exception as e:
        print(e)
        return {"statusCode": 500, "body": str(e)}