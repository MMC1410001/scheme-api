# ZAP Scanning Report

ZAP by [Checkmarx](https://checkmarx.com/).


## Summary of Alerts

| Risk Level | Number of Alerts |
| --- | --- |
| High | 0 |
| Medium | 1 |
| Low | 1 |
| Informational | 2 |




## Insights

| Level | Reason | Site | Description | Statistic |
| --- | --- | --- | --- | --- |
| Low | Warning |  | ZAP errors logged - see the zap.log file for details | 1    |
| Info | Informational | http://host.docker.internal:8001 | Percentage of responses with status code 2xx | 2 % |
| Info | Informational | http://host.docker.internal:8001 | Percentage of responses with status code 4xx | 97 % |
| Info | Informational | http://host.docker.internal:8001 | Percentage of endpoints with content type application/json | 100 % |
| Info | Informational | http://host.docker.internal:8001 | Percentage of endpoints with method GET | 59 % |
| Info | Informational | http://host.docker.internal:8001 | Percentage of endpoints with method POST | 40 % |
| Info | Informational | http://host.docker.internal:8001 | Count of total endpoints | 27    |







## Alerts

| Name | Risk Level | Number of Instances |
| --- | --- | --- |
| Weak Authentication Method | Medium | 1 |
| Information Disclosure - Debug Error Messages | Low | 1 |
| A Client Error response code was returned by the server | Informational | 27 |
| Non-Storable Content | Informational | 4 |




## Alert Detail



### [ Weak Authentication Method ](https://www.zaproxy.org/docs/alerts/10105/)



##### Medium (Medium)

### Description

HTTP basic or digest authentication has been used over an unsecured connection. The credentials can be read and then reused by someone with access to the network.

* URL: http://host.docker.internal:8001/v1/auth/token
  * Node Name: `http://host.docker.internal:8001/v1/auth/token ()({grant_type,client_id,client_secret})`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `www-authenticate: Basic realm="scheme-api"`
  * Other Info: ``


Instances: 1

### Solution

Protect the connection using HTTPS or use a stronger authentication mechanism.

### Reference


* [ https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html ](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)


#### CWE Id: [ 326 ](https://cwe.mitre.org/data/definitions/326.html)


#### WASC Id: 4

#### Source ID: 3

### [ Information Disclosure - Debug Error Messages ](https://www.zaproxy.org/docs/alerts/10023/)



##### Low (Medium)

### Description

The response appeared to contain common error messages returned by platforms such as ASP.NET, and Web-servers such as IIS and Apache. You can configure the list of common debug messages.

* URL: http://host.docker.internal:8001/openapi.json
  * Node Name: `http://host.docker.internal:8001/openapi.json`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `Internal server error`
  * Other Info: ``


Instances: 1

### Solution

Disable debugging messages before pushing to production.

### Reference



#### CWE Id: [ 1295 ](https://cwe.mitre.org/data/definitions/1295.html)


#### WASC Id: 13

#### Source ID: 3

### [ A Client Error response code was returned by the server ](https://www.zaproxy.org/docs/alerts/100000/)



##### Informational (High)

### Description

A response code of 401 was returned by the server.
This may indicate that the application is failing to handle unexpected input correctly.
Raised by the 'Alert on HTTP Response Code Error' script

* URL: http://host.docker.internal:8001
  * Node Name: `http://host.docker.internal:8001`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/
  * Node Name: `http://host.docker.internal:8001/`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/1137866791109847989
  * Node Name: `http://host.docker.internal:8001/1137866791109847989`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/health/
  * Node Name: `http://host.docker.internal:8001/health/`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/openapi.json/
  * Node Name: `http://host.docker.internal:8001/openapi.json/`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1
  * Node Name: `http://host.docker.internal:8001/v1`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/
  * Node Name: `http://host.docker.internal:8001/v1/`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/8744398854654866478
  * Node Name: `http://host.docker.internal:8001/v1/8744398854654866478`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/auth
  * Node Name: `http://host.docker.internal:8001/v1/auth`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/auth/
  * Node Name: `http://host.docker.internal:8001/v1/auth/`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/auth/7002623183823497292
  * Node Name: `http://host.docker.internal:8001/v1/auth/7002623183823497292`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/auth/actuator/health
  * Node Name: `http://host.docker.internal:8001/v1/auth/actuator/health`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/schemes
  * Node Name: `http://host.docker.internal:8001/v1/schemes`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/schemes/
  * Node Name: `http://host.docker.internal:8001/v1/schemes/`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/schemes/8134517670897925437
  * Node Name: `http://host.docker.internal:8001/v1/schemes/8134517670897925437`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/computeMetadata/v1/
  * Node Name: `http://host.docker.internal:8001/computeMetadata/v1/ ()({grant_type,client_id,client_secret})`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/latest/meta-data/
  * Node Name: `http://host.docker.internal:8001/latest/meta-data/ ()({grant_type,client_id,client_secret})`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/metadata/instance
  * Node Name: `http://host.docker.internal:8001/metadata/instance ()({grant_type,client_id,client_secret})`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/metadata/v1
  * Node Name: `http://host.docker.internal:8001/metadata/v1 ()({grant_type,client_id,client_secret})`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/opc/v1/instance/
  * Node Name: `http://host.docker.internal:8001/opc/v1/instance/ ()({grant_type,client_id,client_secret})`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/opc/v2/instance/
  * Node Name: `http://host.docker.internal:8001/opc/v2/instance/ ()({grant_type,client_id,client_secret})`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/openstack/latest/meta_data.json
  * Node Name: `http://host.docker.internal:8001/openstack/latest/meta_data.json ()({grant_type,client_id,client_secret})`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/auth/token
  * Node Name: `http://host.docker.internal:8001/v1/auth/token ()({grant_type,client_id,client_secret})`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `401`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/auth/token
  * Node Name: `http://host.docker.internal:8001/v1/auth/token ()({grant_type,client_id,client_secret})`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `429`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/auth/token/
  * Node Name: `http://host.docker.internal:8001/v1/auth/token/ ()({grant_type,client_id,client_secret})`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/schemes/current-month
  * Node Name: `http://host.docker.internal:8001/v1/schemes/current-month ()({dealer_code,month,year})`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `422`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/schemes/current-month/
  * Node Name: `http://host.docker.internal:8001/v1/schemes/current-month/ ()({dealer_code,month,year})`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `404`
  * Other Info: ``


Instances: 27

### Solution



### Reference



#### CWE Id: [ 388 ](https://cwe.mitre.org/data/definitions/388.html)


#### WASC Id: 20

#### Source ID: 4

### [ Non-Storable Content ](https://www.zaproxy.org/docs/alerts/10049/)



##### Informational (Medium)

### Description

The response contents are not storable by caching components such as proxy servers. If the response does not contain sensitive, personal or user-specific information, it may benefit from being stored and cached, to improve performance.

* URL: http://host.docker.internal:8001/health
  * Node Name: `http://host.docker.internal:8001/health`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `no-store`
  * Other Info: ``
* URL: http://host.docker.internal:8001/openapi.json
  * Node Name: `http://host.docker.internal:8001/openapi.json`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `no-store`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/auth/token
  * Node Name: `http://host.docker.internal:8001/v1/auth/token ()({grant_type,client_id,client_secret})`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `no-store`
  * Other Info: ``
* URL: http://host.docker.internal:8001/v1/schemes/current-month
  * Node Name: `http://host.docker.internal:8001/v1/schemes/current-month ()({dealer_code,month,year})`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `no-store`
  * Other Info: ``


Instances: 4

### Solution

The content may be marked as storable by ensuring that the following conditions are satisfied:
The request method must be understood by the cache and defined as being cacheable ("GET", "HEAD", and "POST" are currently defined as cacheable)
The response status code must be understood by the cache (one of the 1XX, 2XX, 3XX, 4XX, or 5XX response classes are generally understood)
The "no-store" cache directive must not appear in the request or response header fields
For caching by "shared" caches such as "proxy" caches, the "private" response directive must not appear in the response
For caching by "shared" caches such as "proxy" caches, the "Authorization" header field must not appear in the request, unless the response explicitly allows it (using one of the "must-revalidate", "public", or "s-maxage" Cache-Control response directives)
In addition to the conditions above, at least one of the following conditions must also be satisfied by the response:
It must contain an "Expires" header field
It must contain a "max-age" response directive
For "shared" caches such as "proxy" caches, it must contain a "s-maxage" response directive
It must contain a "Cache Control Extension" that allows it to be cached
It must have a status code that is defined as cacheable by default (200, 203, 204, 206, 300, 301, 404, 405, 410, 414, 501).

### Reference


* [ https://datatracker.ietf.org/doc/html/rfc7234 ](https://datatracker.ietf.org/doc/html/rfc7234)
* [ https://datatracker.ietf.org/doc/html/rfc7231 ](https://datatracker.ietf.org/doc/html/rfc7231)
* [ https://www.w3.org/Protocols/rfc2616/rfc2616-sec13.html ](https://www.w3.org/Protocols/rfc2616/rfc2616-sec13.html)


#### CWE Id: [ 524 ](https://cwe.mitre.org/data/definitions/524.html)


#### WASC Id: 13

#### Source ID: 3


