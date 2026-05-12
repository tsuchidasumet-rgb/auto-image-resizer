import json
import boto3
import os
from botocore.exceptions import ClientError

s3 = boto3.client('s3', region_name='ap-northeast-1')
cognito = boto3.client('cognito-idp', region_name='ap-northeast-1')

BUCKET = os.environ['BUCKET_NAME']
USER_POOL_CLIENT_ID = os.environ['USER_POOL_CLIENT_ID']

def lambda_handler(event, context):
    method = event.get('requestContext', {}).get('http', {}).get('method', '')
    path   = event.get('rawPath', '')

    routes = {
        ('POST', '/auth/register'): handle_register,
        ('POST', '/auth/login'):    handle_login,
        ('POST', '/auth/confirm'):  handle_confirm,
        ('POST', '/upload'):        handle_upload,
        ('GET',  '/images'):        handle_list_images,
    }

    handler = routes.get((method, path))
    if handler:
        return handler(event)
    return response(404, {'error': 'Not found'})


def handle_register(event):
    try:
        body = json.loads(event.get('body', '{}'))
        email    = body['email']
        password = body['password']
        nickname = body['nickname']

        cognito.sign_up(
            ClientId=USER_POOL_CLIENT_ID,
            Username=email,
            Password=password,
            UserAttributes=[
                {'Name': 'email',    'Value': email},
                {'Name': 'nickname', 'Value': nickname},
            ]
        )
        return response(200, {'message': 'สมัครสมาชิกสำเร็จ กรุณาตรวจสอบอีเมลเพื่อยืนยัน'})

    except cognito.exceptions.UsernameExistsException:
        return response(400, {'error': 'อีเมลนี้มีผู้ใช้งานแล้ว'})
    except Exception as e:
        return response(500, {'error': str(e)})


def handle_confirm(event):
    """ยืนยัน OTP จากอีเมล"""
    try:
        body = json.loads(event.get('body', '{}'))
        cognito.confirm_sign_up(
            ClientId=USER_POOL_CLIENT_ID,
            Username=body['email'],
            ConfirmationCode=body['code']
        )
        return response(200, {'message': 'ยืนยันอีเมลสำเร็จ'})
    except Exception as e:
        return response(400, {'error': str(e)})


def handle_login(event):
    try:
        body = json.loads(event.get('body', '{}'))
        result = cognito.initiate_auth(
            ClientId=USER_POOL_CLIENT_ID,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={
                'USERNAME': body['email'],
                'PASSWORD': body['password'],
            }
        )
        tokens = result['AuthenticationResult']
        return response(200, {
            'accessToken':  tokens['AccessToken'],
            'refreshToken': tokens['RefreshToken'],
            'expiresIn':    tokens['ExpiresIn'],
        })
    except cognito.exceptions.NotAuthorizedException:
        return response(401, {'error': 'อีเมลหรือรหัสผ่านไม่ถูกต้อง'})
    except cognito.exceptions.UserNotConfirmedException:
        return response(403, {'error': 'กรุณายืนยันอีเมลก่อนเข้าสู่ระบบ'})
    except Exception as e:
        return response(500, {'error': str(e)})


def get_username_from_token(access_token):
    """ดึง nickname จาก token"""
    user = cognito.get_user(AccessToken=access_token)
    attrs = {a['Name']: a['Value'] for a in user['UserAttributes']}
    return attrs.get('nickname', 'Unknown Seller')


def handle_upload(event):
    try:
        # 1. ดึง token จาก header
        headers = event.get('headers', {})
        auth_header = headers.get('authorization', '')
        access_token = auth_header.replace('Bearer ', '')

        # 2. ดึงข้อมูล User จาก Cognito ด้วย Access Token
        user_data = cognito.get_user(AccessToken=access_token)
        
        # 3. เจาะหาค่า nickname จาก UserAttributes
        # Cognito เก็บข้อมูลเป็น list เช่น [{'Name': 'nickname', 'Value': 'Game'}, ...]
        attributes = {attr['Name']: attr['Value'] for attr in user_data.get('UserAttributes', [])}
        nickname = attributes.get('nickname', 'Unknown Seller') 

        # 4. จัดการข้อมูลไฟล์
        body = json.loads(event.get('body', '{}'))
        filename     = body.get('filename', 'image.jpg')
        content_type = body.get('contentType', 'image/jpeg')
        key = f"uploads/{filename}"

        # 5. สร้าง Presigned URL พร้อมฝัง nickname ลงใน Metadata
        presigned_url = s3.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': BUCKET,
                'Key': key,
                'ContentType': content_type,
                'Metadata': {
                    'username': nickname  # ส่งค่า nickname ไปแทน username เดิม
                }
            },
            ExpiresIn=300
        )
        return response(200, {'uploadUrl': presigned_url, 'key': key})

    except cognito.exceptions.NotAuthorizedException:
        return response(401, {'error': 'Token หมดอายุ กรุณา login ใหม่'})
    except Exception as e:
        return response(500, {'error': str(e)})


def handle_list_images():
    try:
        result   = s3.list_objects_v2(Bucket=BUCKET, Prefix='processed/')
        contents = result.get('Contents', [])
        images   = []
        for obj in contents:
            key = obj['Key']
            if key.endswith('/'): continue
            url = s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': BUCKET, 'Key': key},
                ExpiresIn=3600
            )
            images.append({
                'key': key,
                'filename': key.replace('processed/', ''),
                'url': url,
                'size': obj['Size'],
                'lastModified': obj['LastModified'].isoformat()
            })
        images.sort(key=lambda x: x['lastModified'], reverse=True)
        return response(200, {'images': images})
    except Exception as e:
        return response(500, {'error': str(e)})


def response(status_code, body):
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(body, ensure_ascii=False)
    }