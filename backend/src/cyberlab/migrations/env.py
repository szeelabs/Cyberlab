from alembic import context

from cyberlab.db import Base

engine = context.config.attributes["engine"]

with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,  # SQLite needs table rebuilds for ALTERs
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()
