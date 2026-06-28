import sqlite3

def iniciar_banco():
    conexao = sqlite3.connect('sistema.db')
    cursor = conexao.cursor()

    # Recria a tabela limpa
    cursor.execute('DROP TABLE IF EXISTS ingredientes')
    
    cursor.execute('''
    CREATE TABLE ingredientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        preco_kg REAL NOT NULL,
        calorias_100g REAL NOT NULL,
        carboidratos_100g REAL NOT NULL,
        proteinas_100g REAL NOT NULL,
        gorduras_100g REAL NOT NULL,
        sodio_100g REAL NOT NULL
    )
    ''')

    # Alimentos extraídos da Tabela Oficial TACO UNICAMP (Valores por 100g)
    # Definimos um preço padrão inicial de R$ 10.00/kg que você poderá alterar na tela
    dados_taco = [
        # Cereais e Derivados
        ('Arroz Integral Cozido (TACO)', 8.50, 124.0, 25.8, 2.6, 1.0, 1.0),
        ('Arroz Tipo 1 Cozido (TACO)', 6.00, 128.0, 28.1, 2.5, 0.2, 1.0),
        ('Farinha de Trigo integral (TACO)', 9.00, 339.0, 72.9, 12.0, 1.8, 3.0),
        ('Farinha de Trigo Tipo 1 (TACO)', 5.50, 360.0, 75.1, 9.8, 1.4, 1.0),
        ('Aveia em Flocos (TACO)', 14.00, 394.0, 66.6, 13.9, 8.5, 5.0),
        ('Amido de Milho (TACO)', 8.00, 361.0, 87.1, 0.6, 0.3, 8.0),
        # Leguminosas e Proteínas
        ('Feijão Carioca Cozido (TACO)', 9.00, 76.0, 13.6, 4.8, 0.5, 2.0),
        ('Frango Peito Sem Pele Cozido (TACO)', 22.00, 163.0, 0.0, 31.5, 3.2, 53.0),
        ('Carne Patinho Patinho Sem Gordura Grelhado (TACO)', 38.00, 219.0, 0.0, 35.9, 7.3, 63.0),
        ('Ovo de Galinha Inteiro Cru (TACO)', 14.00, 143.0, 1.6, 13.0, 8.9, 168.0),
        # Laticínios e Óleos
        ('Leite Integral UHT (TACO)', 5.50, 60.0, 4.7, 3.1, 3.3, 50.0),
        ('Manteiga Com Sal (TACO)', 48.00, 726.0, 0.4, 0.4, 82.2, 577.0),
        ('Óleo de Soja Recipiente (TACO)', 10.00, 884.0, 0.0, 0.0, 100.0, 0.0),
        ('Queijo Muçarela (TACO)', 42.00, 330.0, 3.0, 22.6, 25.2, 580.0),
        # Açúcares e Diversos
        ('Açúcar Refinado (TACO)', 4.50, 387.0, 99.9, 0.0, 0.0, 0.0),
        ('Chocolate em Pó Solúvel (TACO)', 28.00, 412.0, 79.1, 6.1, 5.8, 48.0),
        ('Sal de Cozinha Refinado (TACO)', 3.00, 0.0, 0.0, 0.0, 0.0, 38758.0),
        ('Água de Torneira (TACO)', 0.00, 0.0, 0.0, 0.0, 0.0, 4.0)
    ]

    cursor.executemany('''
    INSERT INTO ingredientes (nome, preco_kg, calorias_100g, carboidratos_100g, proteinas_100g, gorduras_100g, sodio_100g)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', dados_taco)

    conexao.commit()
    conexao.close()
    print("Base de dados atualizada com ingredientes da TACO!")

if __name__ == '__main__':
    iniciar_banco()
