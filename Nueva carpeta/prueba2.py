import requests
import json

# URL de la API para crear una factura
url = "https://api.tecopos.com/api/v1/administration/billing-order"

# Datos de la factura
data = {
    "products": [
        {
            "productId": 8834,
            "quantity": 1,
            "priceUnitary": {
                "amount": 252.5,
                "codeCurrency": "CUP"
            }
        }
    ],
    "areaSalesId": {
        "id": 264,
        "name": "Venta",
        "status": "PAYMENT_PENDING",
        "discount": 0,
        "commission": 0,
        "observations": None,
    },
    "amountReturned": None,
    "areaSales": {
        "id": 264,
        "name": "Venta"
    },
    "client": {
        "id": 17417,
        "firstName": "Prueba",
        "lastName": "Prueba",
        "observations": None,
        "email": None,
        "ci": None,
        "barCode": "C_001700132",
        "address": {
            "id": 13763,
            "street_1": None,
            "street_2": None,
            "description": None,
            "city": None,
            "postalCode": None
        },
        "phones": [
            {
                "id": 13666,
                "number": None,
                "description": None,
                "codeCountry": None
            }
        ]
    },
    "billing": None,
    "shipping": None,
    "shippingPrice": None,
    "tipPrice": None,
    "amountReturned": None,
    "couponDiscountPrice": None,
    "shippingBy": None,
    "dispatch": None,
    "paymentGateway": None,
    "listResources": [],
    "coupons": [],
    "selledProducts": [
        {
            "quantity": 1,
            "id": 14421268,
            "name": "Bocadito Cerdo",
            "status": "RECEIVED",
            "observations": None,
            "type": "MENU",
            "areaId": None,
            "productionAreaId": 431,
            "productionTicketId": None,
            "productId": 8834,
            "variationId": None,
            "totalCost": 89.7,
            "modifiedPrice": False,
            "measure": "UNIT",
            "priceTotal": {
                "amount": 252.5,
                "codeCurrency": "CUP"
            }
        }
    ],
    "currenciesPayment": []
}

# Cabeceras de la solicitud, si es necesario (incluir tu token o autenticación si la necesitas)
headers = {
    "Authorization": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6MzMsImlhdCI6MTc1MTI1ODUwOCwiZXhwIjoxNzUzODUwNTA4fQ.INWTCL-5mpU0lhttcFxOyJLKohRI4mcmhFT1uz4M2so",  # Reemplaza con tu token válido
    "Content-Type": "application/json"
}

# Enviar la solicitud POST para crear la factura
response = requests.post(url, headers=headers, data=json.dumps(data))

# Verificar la respuesta
if response.status_code == 201:
    print("Factura generada exitosamente.")
    print("Datos de la factura:", response.json())
else:
    print(f"Error al generar la factura: {response.status_code}")
    try:
        error_details = response.json()  # Obtener detalles de la respuesta de error
        print("Detalles del error:", error_details)
    except json.JSONDecodeError:
        print("No se pudo decodificar la respuesta de error.")
        print("Respuesta completa:", response.text)
