from app.db import Base, engine
from app import models  # noqa: F401  (importing registers the tables)

if __name__ == "__main__":
    Base.metadata.create_all(engine)
    print("Tables created.")