import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Política de privacidad · Contact Outreach",
};

// Public page: Google's OAuth consent screen links to it, so it must render without a session.
export default function PrivacyPage() {
  return (
    <main className="legal-page">
      <article className="legal-page__document">
        <div className="login-panel__brand type-micro">Contact Outreach</div>
        <h1 className="type-display">Política de privacidad</h1>
        <p>
          Contact Outreach es una herramienta privada que usamos para escribirles a negocios que podrían estar
          interesados en nuestros productos y para ordenar sus respuestas. Acá explicamos qué datos usa, para qué y qué
          podés pedirnos.
        </p>

        <h2 className="type-title">Datos de los negocios</h2>
        <p>
          Usamos datos públicos de negocios: nombre, rubro, dirección, sitio web y direcciones de correo publicadas por
          el propio negocio. Los obtenemos de catálogos abiertos de lugares y de las páginas web de cada negocio. No
          compramos listas de correo.
        </p>

        <h2 className="type-title">Datos de la cuenta de Gmail</h2>
        <p>
          Quien usa la herramienta conecta su propia cuenta de Gmail y autoriza dos permisos: enviar correos en su nombre
          y leer los correos de su casilla. Los usamos solamente para enviar los mensajes que una persona aprobó y para
          encontrar las respuestas a esos mensajes y los correos de clientes que ya están en contacto. El permiso
          guardado se almacena cifrado.
        </p>
        <p>
          El uso y la transferencia de la información recibida de las API de Google cumplen la{" "}
          <a href="https://developers.google.com/terms/api-services-user-data-policy" rel="noreferrer">
            Política de datos del usuario de los servicios de API de Google
          </a>
          , incluidos los requisitos de uso limitado.
        </p>

        <h2 className="type-title">Inteligencia artificial</h2>
        <p>
          Si está activada, la herramienta envía a un servicio externo de inteligencia artificial los datos públicos de
          un negocio, para descartar los que no coinciden con lo que buscamos, y el texto de las respuestas recibidas,
          para ordenarlas y sugerir qué hacer. No escribe el primer correo a un negocio ni agrega negocios a la lista:
          solo puede descartar, y una persona revisa y aprueba cada campaña antes de enviarla.
        </p>

        <h2 className="type-title">Qué no hacemos</h2>
        <p>
          No vendemos ni compartimos datos con terceros para publicidad y no rastreamos si abrís un correo.
        </p>

        <h2 className="type-title">Pedir la baja</h2>
        <p>
          Si recibiste un correo nuestro y no querés recibir más, respondelo con palabras como «baja», «darme de baja»
          o «no me escriban». La herramienta lo detecta, bloquea esa dirección para siempre y esa decisión no se
          revierte. Cualquier otra respuesta también detiene los recordatorios de esa campaña.
        </p>

        <h2 className="type-title">Pedir que borremos tus datos</h2>
        <p>
          Respondé el correo que te enviamos pidiendo que borremos tus datos. Eliminamos de la herramienta el
          nombre, la dirección, el teléfono y la web del negocio, el nombre de la persona de contacto, el texto de
          los correos y lo que analizó la inteligencia artificial, y borramos la conversación de nuestra casilla de
          Gmail. Conservamos solo tu dirección de correo, para no volver a escribirte nunca. Las copias de seguridad
          que ya existían pueden conservar esos datos hasta que se reemplacen.
        </p>
        <p>Para cualquier otra consulta sobre tus datos, respondé el correo que te enviamos.</p>

        <p className="legal-page__footer">
          <Link href="/">Volver al inicio</Link>
        </p>
      </article>
    </main>
  );
}
