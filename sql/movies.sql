CREATE TABLE IF NOT EXISTS movies (
    id int,
    type varchar(255),
    name varchar(255),
    year int
);

TRUNCATE TABLE movies;

INSERT INTO movies (id, type, name, year) VALUES (11, 'movie', 'Titanic', 1997);