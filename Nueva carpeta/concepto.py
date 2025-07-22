import requests

class TecoposAPI:
    def __init__(self, token):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "*/*",
            "x-app-origin": "Tecopos-Admin"
        }

    def get_accounts(self):
        url = "https://api3.tecopos.com/api/v1/administration/bank/account"
        resp = requests.get(url, headers=self.headers)
        if resp.status_code == 200:
            return resp.json().get("items", [])
        else:
            print(f"❌ Error buscando cuentas: {resp.status_code} {resp.text}")
            return []

    def find_account(self, name):
        accounts = self.get_accounts()
        for a in accounts:
            if a.get("name") == name:
                return a
        return None

    def create_account(self, name):
        url = "https://api3.tecopos.com/api/v1/administration/bank/account"
        payload = {
            "name": name,
            "address": "180800170028",
            "allowMultiCurrency": True,
            "isActive": True,
            "isBlocked": False,
            "isPrivate": True,
            "allowSubAccounts": False,
            "isSubAccount": False
        }
        resp = requests.post(url, headers=self.headers, json=payload)
        if resp.status_code == 201:
            print(f"✅ Cuenta '{name}' creada.")
            return resp.json()
        else:
            print(f"❌ Error creando cuenta: {resp.status_code} {resp.text}")
            return None

    def get_tags(self, account_id):
        url = f"https://api3.tecopos.com/api/v1/administration/bank/tag/{account_id}"
        resp = requests.get(url, headers=self.headers)
        if resp.status_code == 200:
            return resp.json().get("items", [])
        else:
            print(f"❌ Error obteniendo conceptos: {resp.status_code} {resp.text}")
            return []

    def find_tag(self, account_id, name):
        tags = self.get_tags(account_id)
        for t in tags:
            if t.get("name") == name:
                return t
        return None

    def create_tag(self, account_id, name):
        url = f"https://api3.tecopos.com/api/v1/administration/bank/tag/{account_id}"
        payload = {"name": name, "code": None}
        resp = requests.post(url, headers=self.headers, json=payload)
        if resp.status_code == 201:
            print(f"✅ Concepto '{name}' creado.")
            return resp.json()
        else:
            print(f"❌ Error creando concepto: {resp.status_code} {resp.text}")
            return None

    def create_operation(self, account_id, tag_id, amount, currency, operation="debit", description=""):
        url = f"https://api3.tecopos.com/api/v1/administration/bank/account/{account_id}/operation"
        payload = {
            "operation": operation,
            "accountTag": tag_id,
            "amount": {
                "amount": amount,
                "codeCurrency": currency
            },
            "description": description,
            "blocked": False
        }
        resp = requests.post(url, headers=self.headers, json=payload)
        if resp.status_code == 201:
            print(f"✅ Operación creada: {operation} {amount} {currency}")
            return resp.json()
        else:
            print(f"❌ Error creando operación: {resp.status_code} {resp.text}")
            return None


def main():
    token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6MzMsImlhdCI6MTc1MjA2NzYzNiwiZXhwIjoxNzUyNjcyNDM2fQ.5g793s82oWTSFNgrQhq2b3Vwyv8Ht8mysyDLt4AzLaY"

    api = TecoposAPI(token)

    nombre_cuenta = "prueba"
    nombre_concepto = "Depósito efectivo"
    monto = 1500
    moneda = "CUP"
    tipo_operacion = "debit"
    descripcion = "Pago con concepto correcto"

    cuenta = api.find_account(nombre_cuenta)
    if not cuenta:
        cuenta = api.create_account(nombre_cuenta)
        if not cuenta:
            print("No se pudo crear ni encontrar la cuenta.")
            return

    cuenta_id = cuenta["id"]

    tag = api.find_tag(cuenta_id, nombre_concepto)
    if not tag:
        tag = api.create_tag(cuenta_id, nombre_concepto)
        if not tag:
            print("No se pudo crear ni encontrar el concepto.")
            return

    operacion = api.create_operation(cuenta_id, tag["id"], monto, moneda, tipo_operacion, descripcion)
    if operacion:
        print("Operación completada exitosamente.")
    else:
        print("Fallo al crear la operación.")


if __name__ == "__main__":
    main()
