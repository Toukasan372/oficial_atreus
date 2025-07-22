import requests

url = "https://api3.tecopos.com/api/v1/administration/bank/account"

headers = {
    "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6MzMsImlhdCI6MTc1MjA2NTMzNCwiZXhwIjoxNzUyNjcwMTM0fQ.8WuBHl0QAk3M3c9zXTqyXATC2iTTOdB7NihjiriyXa8",
    "Content-Type": "application/json",
    "Accept": "*/*",
    "x-app-origin": "Tecopos-Admin"
}

data = {
    "name": "Cuenta Banco Metropolitano",
    "address": "180800170028",         # Puede ser número de cuenta
    "isPrivate": True,
    "isActive": True,
    "businessId": 17,                  # El ID del negocio al que se vincula
    "allowMultiCurrency": True,
    "allowSubAccounts": False,
    "isSubAccount": False
}

response = requests.post(url, headers=headers, json=data)

if response.status_code == 201:
    print("✅ Cuenta bancaria creada con éxito:")
    print(response.json())
else:
    print("❌ Error al crear cuenta:")
    print("Código:", response.status_code)
    print("Respuesta:", response.text)
